"""M0.14b: opt-in, read-only SSD1327 player display.

No Sonos transport commands. No startup of player service. The user's PNG is
loaded once, optionally; if absent a plain status screen is shown.
"""
from __future__ import annotations
import argparse
from contextlib import nullcontext
import math
import json
from pathlib import Path
import socket
import time
from PIL import Image, ImageDraw, ImageFont, ImageOps
from .ssd1327 import SSD1327, DisplayConfig, Framebuffer
from .oled_ambient import OLEDAmbientConfig, OLEDContrastControl

DEFAULT_SOCKET = '/tmp/sonoscdplayer-control.sock'
DEFAULT_LOGO = Path(__file__).resolve().parents[2] / 'data' / 'start_screen.png'


def request_status(path=DEFAULT_SOCKET, timeout=1.5):
    """Strictly read-only, bounded socket read; no replays or state mutation."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        s.connect(path)
        s.sendall(b'{"command":"status"}\n')
        response = bytearray()
        while b'\n' not in response:
            if len(response) >= 8192:
                raise ValueError('status response too large')
            block = s.recv(min(2048, 8193-len(response)))
            if not block:
                raise ValueError('status response truncated')
            response.extend(block)
    payload = json.loads(response.split(b'\n',1)[0])
    if payload.get('ok') is not True or not isinstance(payload.get('state'), dict):
        raise ValueError('invalid player status')
    return payload['state']


def image_to_frame(im):
    """Convert RGBA/RGB/greyscale to a centered 128px 4-bit framebuffer."""
    if im.width < 1 or im.height < 1 or im.width > 4096 or im.height > 4096:
        raise ValueError('invalid image dimensions')
    rgba=im.convert('RGBA')
    backdrop=Image.new('RGBA', rgba.size,(0,0,0,255))
    rgba=Image.alpha_composite(backdrop,rgba).convert('L')
    fitted=ImageOps.contain(rgba,(128,128),method=Image.Resampling.LANCZOS)
    canvas=Image.new('L',(128,128),0)
    canvas.paste(fitted,((128-fitted.width)//2,(128-fitted.height)//2))
    fb=Framebuffer(); fb.data[:]=bytes(((canvas.getpixel((x,y))*15+127)//255<<4) | ((canvas.getpixel((x+1,y))*15+127)//255) for y in range(128) for x in range(0,128,2))
    return fb


def load_splash(path):
    try:
        with Image.open(path) as im:
            im.load()
            return image_to_frame(im)
    except (FileNotFoundError, OSError, ValueError):
        return None


def short_text(value, max_chars=20):
    return str(value or '')[:max_chars]


def drive_wait_frame():
    """Read-only temporary OLED notice while a verified drive wake runs."""
    img=Image.new('L',(128,128),0)
    draw=ImageDraw.Draw(img)
    font=ImageFont.load_default()
    draw.text((5,4),'SONOS CD PLAYER',font=font,fill=255)
    draw.line((4,19,123,19),fill=88)
    draw.text((13,45),'PLEASE WAIT',font=font,fill=255)
    draw.text((9,66),'Starting CD drive',font=font,fill=180)
    draw.line((4,121,123,121),fill=66)
    return image_to_frame(img)


def drive_fault(state):
    """Show only confirmed, latched motor wake failures, not stale generic errors."""
    return (isinstance(state, dict)
            and isinstance(state.get('drive_power'), dict)
            and state['drive_power'].get('idle_wake_fault_latched') is True)


def drive_fault_frame():
    """Persistent read-only motor fault notice; no automatic retries."""
    img=Image.new('L',(128,128),0)
    draw=ImageDraw.Draw(img)
    font=ImageFont.load_default()
    draw.text((5,4),'SONOS CD PLAYER',font=font,fill=255)
    draw.line((4,19,123,19),fill=88)
    draw.text((9,44),'CD DRIVE ERROR',font=font,fill=255)
    draw.text((8,65),'Wake failed',font=font,fill=210)
    draw.text((8,85),'Check USB power',font=font,fill=180)
    draw.line((4,121,123,121),fill=66)
    return image_to_frame(img)


def drive_waking(state):
    return (isinstance(state, dict)
            and isinstance(state.get('drive_power'), dict)
            and state['drive_power'].get('idle_waking') is True)


def status_frame(state=None, error=None):
    """Simple legible OLED status; avoid claiming live Sonos reachability."""
    if error is None and drive_fault(state):
        return drive_fault_frame()
    if error is None and drive_waking(state):
        return drive_wait_frame()
    img=Image.new('L',(128,128),0)
    draw=ImageDraw.Draw(img)
    f=ImageFont.load_default()
    draw.text((5,4),'SONOS CD PLAYER',font=f,fill=255)
    draw.line((4,19,123,19),fill=88)
    if error is not None:
        draw.text((6,32),'SERVICE OFFLINE',font=f,fill=245)
        draw.text((6,53),'No local status',font=f,fill=155)
        draw.text((6,78),'Waiting...',font=f,fill=155)
    else:
        player=state.get('player') or {}
        sonos=state.get('sonos') or {}
        stream=state.get('stream') or {}
        mode=short_text(player.get('mode','UNKNOWN'),17)
        draw.text((6,29),mode,font=f,fill=255)
        track=player.get('track')
        count=player.get('track_count')
        track=str(track) if isinstance(track,int) and track>0 else '--'
        count=str(count) if isinstance(count,int) and count>0 else '--'
        draw.text((6,52),f'Track {track}/{count}',font=f,fill=215)
        # Cached transport is NOT an independent reachability check.
        transport=short_text(sonos.get('transport') or 'unknown',16)
        draw.text((6,75),'Sonos: '+transport,font=f,fill=154)
        draw.text((6,98),'Stream: '+('active' if stream.get('active') else 'idle'),font=f,fill=154)
    draw.line((4,121,123,121),fill=66)
    return image_to_frame(img)


def modern_status_frame(state=None, error=None, *, cover_mode='auto', language='de'):
    """Opt-in 128x128 Modern Hi-Fi frame, reusing the existing OLED lifecycle.

    This adapter is intentionally read-only. No discovery, GPIO listening,
    player writes or speculative album/cover/target metadata.
    """
    if error is not None or state is None:
        return status_frame(error=error or 'No local status')
    if drive_fault(state):
        return drive_fault_frame()
    if drive_waking(state):
        return drive_wait_frame()
    from sonos_ui.player_adapter import project_status
    from sonos_ui.render import Renderer
    display = project_status(state)
    image = Renderer(cover_mode=cover_mode,language=language).render(display, grayscale=True)
    return image_to_frame(image)


class StatusHold:
    """Keep last good snapshot briefly across a temporary local socket failure."""
    def __init__(self, grace_seconds=4.0):
        self.grace_seconds = grace_seconds
        self.last_state = None
        self.last_success = None

    def success(self, state, now):
        self.last_state = state
        self.last_success = now
        return state

    def failure(self, now, grace_override=None):
        grace = self.grace_seconds if grace_override is None else grace_override
        if (self.last_state is not None and self.last_success is not None
                and now - self.last_success < grace):
            return self.last_state
        return None


def wait_for_player_ready(status_request, socket_path, *, max_wait_seconds=30.0, poll_seconds=0.25, clock=time.monotonic, sleep=time.sleep):
    """Wait for first valid player snapshot. Never infer Sonos PLAYING from socket readiness.

    Returns a validated state, or None after timeout. Does not mutate player state.
    """
    deadline = clock() + max_wait_seconds
    while True:
        try:
            state = status_request(socket_path)
            if isinstance(state, dict) and isinstance(state.get('player'), dict) and isinstance(state['player'].get('mode'), str):
                return state
        except (OSError, ValueError, TimeoutError, TypeError):
            pass
        remaining = deadline - clock()
        if remaining <= 0:
            return None
        sleep(min(poll_seconds, remaining))


def main(argv=None):
    p=argparse.ArgumentParser(description='M0.14b read-only SSD1327 OLED status')
    p.add_argument('--gpio',action='store_true')
    p.add_argument('--ui-config',type=Path,default=None,
                   help='read-only UI TOML; defaults to /home/pi/sonoscdplayer/ui.toml')
    p.add_argument('--cover',choices=('auto','on','off'),default=None,
                   help='Modern Hi-Fi cover layout: auto (off for 128x128), on, off')
    p.add_argument('--ui-modern',action='store_true',
                   help='opt-in Modern Hi-Fi read-only player status rendering')
    p.add_argument('--execute',action='store_true')
    p.add_argument('--ui-target-rpc',action='store_true',
                   help='experimental: explicitly enable set-target on menu confirmation')
    p.add_argument('--ui-events-socket',default=None,
                   help='private encoder event socket; requires --ui-modern')
    p.add_argument('--socket',default=DEFAULT_SOCKET)
    p.add_argument('--splash',default=str(DEFAULT_LOGO))
    p.add_argument('--adopt-boot-splash',action='store_true',help='Adopt firmware-initialized OLED without reset; requires verified firmware splash')
    p.add_argument('--splash-seconds',type=float,default=None,help='Legacy fixed splash duration; default waits for a valid player status')
    p.add_argument('--splash-ready-timeout',type=float,default=30.0,help='Maximum wait for player status, then show SERVICE OFFLINE')
    p.add_argument('--seconds',type=float,default=30)
    p.add_argument('--refresh-ms',type=int,default=500)
    p.add_argument('--offline-grace-seconds',type=float,default=4.0)
    p.add_argument('--spi-hz',type=int,default=2_000_000)
    p.add_argument('--contrast',type=int,default=128)
    p.add_argument('--oled-recover-on-error',action='store_true',
                   help='opt-in: one full reinitialization after a reported SPI OSError')
    p.add_argument('--ambient-auto',action='store_true',help='Enable independent APDS9306 OLED contrast control')
    p.add_argument('--ambient-socket',default='/run/apds9306.sock')
    p.add_argument('--ambient-timeout-ms',type=int,default=150)
    p.add_argument('--ambient-poll-ms',type=int,default=1000)
    p.add_argument('--ambient-stale-ms',type=int,default=5000)
    p.add_argument('--ambient-smoothing-ms',type=int,default=2000)
    p.add_argument('--ambient-fallback-contrast',type=int,default=128)
    args=p.parse_args(argv)
    from sonos_ui.ui_config import load_ui_config, DEFAULT_CONFIG
    try:
        ui_config = load_ui_config(args.ui_config or DEFAULT_CONFIG)
    except (ValueError, OSError) as exc:
        p.error('invalid UI configuration: '+str(exc))
    cover_mode = args.cover if args.cover is not None else ui_config.cover_mode
    language = ui_config.language
    if args.gpio != args.execute: p.error('--gpio and --execute must be given together')
    if args.ui_target_rpc and not (args.ui_events_socket and args.ui_modern):
        p.error('--ui-target-rpc requires --ui-modern and --ui-events-socket')
    if args.ui_events_socket and not args.ui_modern:
        p.error('--ui-events-socket requires --ui-modern')
    try:
        ambient_config=OLEDAmbientConfig(socket_path=args.ambient_socket,
            timeout_ms=args.ambient_timeout_ms,poll_ms=args.ambient_poll_ms,
            stale_ms=args.ambient_stale_ms,smoothing_ms=args.ambient_smoothing_ms,
            fallback_contrast=args.ambient_fallback_contrast)
        DisplayConfig(spi_hz=args.spi_hz,contrast=args.contrast)
    except ValueError as exc: p.error(str(exc))
    if ((args.splash_seconds is not None and (not math.isfinite(args.splash_seconds) or not 0 <= args.splash_seconds <= 30))
            or not math.isfinite(args.splash_ready_timeout) or not 0 < args.splash_ready_timeout <= 300
            or not math.isfinite(args.seconds) or args.seconds < 0 or args.seconds > 3600):
        p.error('invalid duration')
    if not 200 <= args.refresh_ms <= 10000: p.error('refresh-ms must be 200..10000')
    if not 0 <= args.offline_grace_seconds <= 30: p.error('offline grace must be 0..30 seconds')
    driver=SSD1327(DisplayConfig(spi_hz=args.spi_hz,contrast=args.contrast)) if args.execute else None
    splash=load_splash(args.splash)
    ambient=OLEDContrastControl(ambient_config) if args.ambient_auto else None
    last_contrast=args.contrast
    recovery = None
    last_frame = None
    if args.oled_recover_on_error and args.execute:
        from .oled_recovery import RecoveryBudget
        recovery = RecoveryBudget(attempts=1)

    def write_frame(frame, *, force=False):
        nonlocal last_frame
        if driver is None:
            return
        try:
            driver.show(frame)
            last_frame = frame
        except OSError:
            if recovery is None or not recovery.run(driver, frame, contrast=last_contrast):
                raise
            last_frame = frame

    def adjust_contrast(value):
        if driver is None:
            return
        try:
            driver.set_contrast(value)
        except OSError:
            if recovery is None or last_frame is None or not recovery.run(
                    driver, last_frame, contrast=value):
                raise

    menu = None
    receiver_context = nullcontext(None)
    if args.ui_events_socket:
        from sonos_ui.interactive_menu import InteractiveMenu
        from sonos_ui.ui_event_bridge import EventReceiver
        if args.ui_target_rpc:
            from sonos_ui.target_rpc_client import submit_target
            sender = lambda uid: submit_target(uid, socket_path=args.socket)
            group_sender = lambda uid, members: submit_target(
                uid, members=members, socket_path=args.socket)
        else:
            sender = None
            group_sender = None
        menu = InteractiveMenu(language=language, target_rpc=sender,
                               group_target_rpc=group_sender)
        receiver_context = EventReceiver(args.ui_events_socket)
    try:
        if driver: driver.open(adopt_existing=args.adopt_boot_splash)
        end=time.monotonic()+args.seconds if args.seconds else None
        previous=None
        hold=StatusHold(args.offline_grace_seconds)
        ready_state=None
        if not args.adopt_boot_splash and splash is not None and args.splash_seconds != 0:
            print(json.dumps({'screen':'splash','source':args.splash,'hardware':args.execute}),flush=True)
            write_frame(splash)
            if args.splash_seconds is None:
                # The display changes on real status readiness, not an arbitrary delay.
                ready_state=wait_for_player_ready(request_status, args.socket,
                    max_wait_seconds=args.splash_ready_timeout)
            else:
                time.sleep(args.splash_seconds)
        elif not args.adopt_boot_splash and args.splash_seconds != 0:
            print(json.dumps({'screen':'splash_skipped','reason':'PNG unavailable or invalid'}),flush=True)
        if args.adopt_boot_splash:
            ready_state=wait_for_player_ready(request_status,args.socket,max_wait_seconds=args.splash_ready_timeout)
        if ready_state is not None:
            hold.success(ready_state,time.monotonic())
        with receiver_context as ui_events:
            while end is None or time.monotonic()<end:
                if menu is not None:
                    menu.poll()
                    for _ in range(16):
                        received = ui_events.receive()
                        if received is None:
                            break
                        menu.event(*received)
                if ambient is not None:
                    new_contrast=ambient.tick(time.monotonic()*1000)
                    if new_contrast != last_contrast:
                        adjust_contrast(new_contrast)
                        print(json.dumps({'oled_contrast':new_contrast,'mode':'ambient','hardware':args.execute}),flush=True)
                        last_contrast=new_contrast
                try:
                    state=hold.success(ready_state if ready_state is not None else request_status(args.socket), time.monotonic())
                    ready_state=None
                    frame=(drive_fault_frame() if drive_fault(state) else
                            drive_wait_frame() if drive_waking(state) else
                            menu.frame(image_to_frame) if menu is not None and menu.active else
                           modern_status_frame(state, cover_mode=cover_mode, language=(menu.language if menu is not None else language)) if args.ui_modern else status_frame(state))
                    label={'screen':'status','mode':(state.get('player') or {}).get('mode'),'hardware':args.execute}
                except (OSError,ValueError,TimeoutError,TypeError) as exc:
                    # The player_service socket is single-threaded. While PLAY waits
                    # for initial PCM, status requests can queue for up to 45s.
                    # Keep a previously validated screen temporarily on socket
                    # congestion, but not on missing/refused/unauthorized service.
                    busy = isinstance(exc, (BlockingIOError, TimeoutError, socket.timeout))
                    state=hold.failure(time.monotonic(), 50.0 if busy else None)
                    if state is not None:
                        frame=(drive_wait_frame() if drive_waking(state) else
                            menu.frame(image_to_frame) if menu is not None and menu.active else
                               modern_status_frame(state, cover_mode=cover_mode, language=(menu.language if menu is not None else language)) if args.ui_modern else status_frame(state))
                        label={'screen':'status_cached','mode':(state.get('player') or {}).get('mode'),'hardware':args.execute}
                    else:
                        frame=(menu.frame(image_to_frame) if menu is not None and menu.active
                               else status_frame(error=str(exc)))
                        label={'screen':'service_offline','error':type(exc).__name__,'hardware':args.execute}
                current=bytes(frame.data)
                if current!=previous:
                    write_frame(frame)
                    print(json.dumps(label,ensure_ascii=False),flush=True)
                    previous=current
                time.sleep(args.refresh_ms/1000 if end is None else min(args.refresh_ms/1000,max(0,end-time.monotonic())))
    finally:
        if driver: driver.close()

if __name__=='__main__': main()
