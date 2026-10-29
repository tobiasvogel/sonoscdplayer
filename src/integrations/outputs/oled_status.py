"""M0.14b: opt-in, read-only SSD1327 player display.

No Sonos transport commands. No startup of player service. The user's PNG is
loaded once, optionally; if absent a plain status screen is shown.
"""
from __future__ import annotations
import argparse
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


def status_frame(state=None, error=None):
    """Simple legible OLED status; avoid claiming live Sonos reachability."""
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
    p.add_argument('--execute',action='store_true')
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
    p.add_argument('--ambient-auto',action='store_true',help='Enable independent APDS9306 OLED contrast control')
    p.add_argument('--ambient-socket',default='/run/apds9306.sock')
    p.add_argument('--ambient-timeout-ms',type=int,default=150)
    p.add_argument('--ambient-poll-ms',type=int,default=1000)
    p.add_argument('--ambient-stale-ms',type=int,default=5000)
    p.add_argument('--ambient-smoothing-ms',type=int,default=2000)
    p.add_argument('--ambient-fallback-contrast',type=int,default=128)
    args=p.parse_args(argv)
    if args.gpio != args.execute: p.error('--gpio and --execute must be given together')
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
    try:
        if driver: driver.open(adopt_existing=args.adopt_boot_splash)
        end=time.monotonic()+args.seconds if args.seconds else None
        previous=None
        hold=StatusHold(args.offline_grace_seconds)
        ready_state=None
        if not args.adopt_boot_splash and splash is not None and args.splash_seconds != 0:
            print(json.dumps({'screen':'splash','source':args.splash,'hardware':args.execute}),flush=True)
            if driver: driver.show(splash)
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
        while end is None or time.monotonic()<end:
            if ambient is not None:
                new_contrast=ambient.tick(time.monotonic()*1000)
                if new_contrast != last_contrast:
                    if driver: driver.set_contrast(new_contrast)
                    print(json.dumps({'oled_contrast':new_contrast,'mode':'ambient','hardware':args.execute}),flush=True)
                    last_contrast=new_contrast
            try:
                state=hold.success(ready_state if ready_state is not None else request_status(args.socket), time.monotonic())
                ready_state=None
                frame=status_frame(state)
                label={'screen':'status','mode':(state.get('player') or {}).get('mode'),'hardware':args.execute}
            except (OSError,ValueError,TimeoutError,TypeError) as exc:
                # The player_service socket is single-threaded. While PLAY waits
                # for initial PCM, status requests can queue for up to 45s.
                # Keep a previously validated screen temporarily on socket
                # congestion, but not on missing/refused/unauthorized service.
                busy = isinstance(exc, (BlockingIOError, TimeoutError, socket.timeout))
                state=hold.failure(time.monotonic(), 50.0 if busy else None)
                if state is not None:
                    frame=status_frame(state)
                    label={'screen':'status_cached','mode':(state.get('player') or {}).get('mode'),'hardware':args.execute}
                else:
                    frame=status_frame(error=str(exc))
                    label={'screen':'service_offline','error':type(exc).__name__,'hardware':args.execute}
            current=bytes(frame.data)
            if current!=previous:
                if driver: driver.show(frame)
                print(json.dumps(label,ensure_ascii=False),flush=True)
                previous=current
            time.sleep(args.refresh_ms/1000 if end is None else min(args.refresh_ms/1000,max(0,end-time.monotonic())))
    finally:
        if driver: driver.close()

if __name__=='__main__': main()
