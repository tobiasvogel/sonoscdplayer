"""The display service (sonoscdplayer-oled.service) for the configured OLED backend.

Started with ``python3 -m sonoscdplayer.ui.display_service``. It polls the
player service for its status, receives button and encoder events from the
button service, runs the menu (InteractiveMenu) and draws every frame on the
OLED. It adapts the contrast to the ambient light and shows the start screen.
Playback commands go to the player service; the display service itself
never talks to Sonos.
"""
from __future__ import annotations
import argparse
from contextlib import nullcontext
from dataclasses import replace
from functools import partial
import math
import json
import os
import signal
from pathlib import Path
import socket
import time
from PIL import Image, ImageOps
from sonoscdplayer.system.package_paths import SCREEN_IMAGES
from sonoscdplayer.hardware.displays.frames import image_to_frame
from sonoscdplayer.hardware.displays.runtime import backend_for
from sonoscdplayer.hardware.displays.pins import load_display_pins, DisplayPins
from sonoscdplayer.hardware.displays.ambient_contrast import OLEDAmbientConfig, OLEDContrastControl

DEFAULT_SOCKET = '/tmp/sonoscdplayer-control.sock'
DEFAULT_LOGO = SCREEN_IMAGES / 'start_screen.png'
SHUTDOWN_PRESERVE_MARKER = Path('/run/sonoscdplayer-oled-shutdown-preserve')


def refresh_saved_language(menu, ui_state):
    """Adopt an externally saved menu language only while the menu is idle.

    This is a read-only synchronization point. The local menu remains the
    owner of its current interaction; an external change must not move a
    language picker or interrupt an open device menu.
    """
    if (menu is None or not hasattr(menu, 'languages') or
            getattr(menu, 'active', True) or getattr(menu, 'power_menu', False) or
            getattr(menu, 'language_picker', False) or
            getattr(menu, 'shutdown_screen', None) is not None):
        return False
    saved = ui_state.language(menu.languages)
    if saved is None or saved == menu.language:
        return False
    from sonoscdplayer.ui.renderers.oled_128_speakers import MultiRenderer
    menu.language = saved
    menu.language_focus = menu.languages.index(saved)
    menu.renderer = MultiRenderer(language=saved)
    return True


def preserve_display_on_exit(flag=False, environ=None):
    """Opt-in handover. Exact '1' is used for a systemd Environment drop-in."""
    if environ is None:
        environ = os.environ
    return flag or environ.get('SONOSCDPLAYER_PRESERVE_DISPLAY_ON_EXIT') == '1'


def preserve_display_for_shutdown(flag=False, environ=None, marker_path=SHUTDOWN_PRESERVE_MARKER):
    """Preserve on explicit opt-in or when the shutdown-order unit has armed it.

    The root-owned marker is set only by the separate systemd shutdown sentinel,
    which stops BEFORE the OLED service on system shutdown. Ordinary OLED
    restarts do not stop that sentinel, so they keep the historical blanking.
    """
    return preserve_display_on_exit(flag, environ) or marker_path.is_file()


def player_ready_timeout(default_seconds, *, preserve=False, environ=None):
    """Use a short readiness wait only for an explicitly armed handover resume.

    The ordinary boot path retains its configured timeout. Opt-in is exact '1'
    and requires preserve-display mode, so enabling fast resume alone is inert.
    """
    if environ is None:
        environ = os.environ
    if (preserve and environ.get('SONOSCDPLAYER_HANDOVER_FAST_RESUME') == '1'):
        return min(default_seconds, 2.0)
    return default_seconds


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


def load_splash(path, *, color=False):
    """The start screen image as display frame, or None if it cannot be read."""
    try:
        with Image.open(path) as im:
            im.load()
            return image_to_frame(im, color=color)
    except (FileNotFoundError, OSError, ValueError):
        return None


def start_splash(path, language, *, color=False):
    """Localized start screen for the standard artwork; custom PNGs as-is."""
    try:
        if Path(path).resolve() == DEFAULT_LOGO.resolve():
            from sonoscdplayer.ui.system_screens import screen_image
            return image_to_frame(screen_image('start', language, grayscale=not color), color=color)
    except (OSError, ValueError, KeyError):
        pass  # missing font/artwork: fall back to the plain PNG below
    return load_splash(path, color=color)


def drive_waking(state):
    """Only a confirmed active drive wake gets the waiting notice."""
    return (
        isinstance(state, dict)
        and isinstance(state.get('drive_power'), dict)
        and state['drive_power'].get('idle_waking') is True
    )


def drive_fault(state):
    """Only a latched drive wake failure gets the error notice."""
    return (
        isinstance(state, dict)
        and isinstance(state.get('drive_power'), dict)
        and state['drive_power'].get('idle_wake_fault_latched') is True
    )


def drive_fault_details(state):
    """(seconds until a retry, retries exhausted) from a confirmed latched fault."""
    power = state.get('drive_power') if isinstance(state, dict) else None
    if not isinstance(power, dict):
        return None, False
    retry = power.get('idle_wake_retry_in_seconds')
    if isinstance(retry, bool) or not isinstance(retry, int) or retry < 0:
        retry = None
    return retry, power.get('idle_wake_retries_exhausted') is True


def notice_frame(key, *, severity='info', params=None, language='de', color=False):
    """One Modern Hi-Fi rendering pipeline for transient and persistent notices."""
    from sonoscdplayer.ui.notices.notice import Notice
    from sonoscdplayer.ui.notices.render import NoticeRenderer
    notice = Notice(key, severity=severity, params=params or {})
    return image_to_frame(NoticeRenderer().render(
        notice, language=language, grayscale=not color), color=color)


def drive_notice_frame(state, *, language='de', color=False):
    """Represent verified drive state without changing or retrying drive actions."""
    if drive_fault(state):
        retry, exhausted = drive_fault_details(state)
        if exhausted:
            return notice_frame('status.drive_wake_exhausted',
                                severity='error', language=language, color=color)
        if retry is None:
            return notice_frame('status.drive_wake_fault',
                                severity='error', language=language, color=color)
        if retry > 0:
            return notice_frame('status.drive_wake_retry',
                                severity='error', params={'seconds': retry},
                                language=language, color=color)
        return notice_frame('status.drive_wake_retry_ready',
                            severity='error', language=language, color=color)
    if drive_waking(state):
        return notice_frame('status.drive_waking', language=language, color=color)
    return None


def modern_status_frame(state=None, error=None, *, cover_mode='auto', language='de', color=False):
    """Render the player or a localized Modern Hi-Fi status notice."""
    if error is not None or state is None:
        return notice_frame('status.player_offline', severity='error',
                            language=language, color=color)
    notice = drive_notice_frame(state, language=language, color=color)
    if notice is not None:
        return notice
    from sonoscdplayer.ui.menu.player_adapter import project_status
    from sonoscdplayer.ui.renderers.oled_128 import Renderer
    display = project_status(state)
    image = Renderer(cover_mode=cover_mode, language=language).render(
        display, grayscale=not color)
    return image_to_frame(image, color=color)


def safe_modern_status_frame(state, *, cover_mode='auto', language='de', color=False):
    """Use the notice renderer, never a recursive legacy fallback, on bad data."""
    try:
        return modern_status_frame(
            state, cover_mode=cover_mode, language=language, color=color)
    except (ValueError, TypeError, KeyError):
        return notice_frame('status.invalid_player_status',
                            severity='error', language=language, color=color)


# While a scrolling text overflows its viewport, redraw this often so the
# marquee moves smoothly; otherwise keep the configured --refresh-ms.
MARQUEE_FRAME_SECONDS = 0.1


# While a menu is open the loop runs this often, so turns and presses show at once.
MENU_FRAME_SECONDS = 0.05


def frame_delay(refresh_seconds):
    """Seconds until the next frame: shorter while a text scrolls."""
    from sonoscdplayer.ui.menu.marquee import MARQUEE_CLOCK
    if MARQUEE_CLOCK.animating():
        return min(refresh_seconds, MARQUEE_FRAME_SECONDS)
    return refresh_seconds


def netsetup_shown(menu):
    """True while the menu shows the WLAN setup."""
    check = getattr(menu, 'netsetup_snapshot', None)
    snap = check() if callable(check) else None
    return isinstance(getattr(snap, 'state', None), str)


def menu_overlay_active(menu):
    """True while the volume overlay is shown."""
    check = getattr(menu, 'volume_overlay_active', None)
    return check() is True if callable(check) else False


def selected_status_frame(state, *, menu=None, ui_modern=False,
                          cover_mode='auto', language='de', cached=False, color=False):
    """All runtime views use Modern Hi-Fi, including drive and error notices.

    Retain the historical ui_modern/cached arguments for compatible callers,
    but neither option can re-enable a Legacy rendering branch.
    """
    converter = partial(image_to_frame, color=True) if color else image_to_frame
    def menu_frame():
        return menu.frame(converter, gray=False) if color else menu.frame(converter)
    if menu is not None and (getattr(menu, 'power_menu', False) is True
                             or isinstance(getattr(menu, 'shutdown_screen', None), str)
                             or netsetup_shown(menu)):
        return menu_frame()
    drive_frame = drive_notice_frame(state, language=language, color=color)
    if drive_frame is not None:
        return drive_frame
    if menu is not None and (menu.active or menu_overlay_active(menu)):
        return menu_frame()
    return safe_modern_status_frame(
        state, cover_mode=cover_mode, language=language, color=color)


class StatusHold:
    """Keep last good snapshot briefly across a temporary local socket failure."""
    def __init__(self, grace_seconds=4.0):
        self.grace_seconds = grace_seconds
        self.last_state = None
        self.last_success = None

    def success(self, state, now):
        """Remember a successful player status and return it."""
        self.last_state = state
        self.last_success = now
        return state

    def failure(self, now, grace_override=None):
        """The remembered status during the grace period, else None."""
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
    """Command line of the display service (sonoscdplayer-oled.service)."""
    p=argparse.ArgumentParser(description='Configured OLED status and menu service')
    p.add_argument('--gpio',action='store_true')
    from sonoscdplayer.system.state_files import default_state_dir
    p.add_argument('--state-dir',type=Path,default=default_state_dir(),
                   help='where the language chosen in the menu is remembered '
                        '(default: $STATE_DIRECTORY or /var/lib/sonoscdplayer)')
    p.add_argument('--ui-config',type=Path,default=None,
                   help='read-only UI TOML (default: ui.toml in the standard config directory; '
                        'built-in defaults if absent)')
    p.add_argument('--cover',choices=('auto','on','off'),default=None,
                   help='Modern Hi-Fi cover layout: auto (off for 128x128), on, off')
    p.add_argument('--ui-modern',action='store_true',
                   help='compatibility flag: runtime rendering is always Modern Hi-Fi')
    p.add_argument('--execute',action='store_true')
    p.add_argument('--ui-target-rpc',action='store_true',
                   help='experimental: explicitly enable set-target on menu confirmation')
    p.add_argument('--ui-network-setup',action='store_true',
                   help='CONNECT 10 s / network menu start the WLAN setup hotspot; '
                        'needs the polkit rule from docs/NETWORK_SETUP.md')
    p.add_argument('--ui-power-actions',action='store_true',
                   help='enable the power menu (power off / restart); needs the polkit rule from docs/POWER_BUTTON.md')
    p.add_argument('--ui-events-socket',default=None,
                   help='private encoder event socket; requires --ui-modern')
    p.add_argument('--socket',default=DEFAULT_SOCKET)
    p.add_argument('--splash',default=str(DEFAULT_LOGO))
    p.add_argument('--adopt-boot-splash',action='store_true',default=None,help='Adopt firmware-initialized OLED without reset; requires verified firmware splash')
    p.add_argument('--preserve-display-on-exit',action='store_true',default=None,
                   help='On renderer exit, release SPI/GPIO without DISPLAY OFF; '
                        'requires an explicitly coordinated display handover')
    p.add_argument('--splash-seconds',type=float,default=None,help='fixed splash duration; default waits for a valid player status')
    p.add_argument('--splash-ready-timeout',type=float,default=30.0,help='Maximum wait for player status, then show SERVICE OFFLINE')
    p.add_argument('--seconds',type=float,default=30)
    p.add_argument('--refresh-ms',type=int,default=500)
    p.add_argument('--offline-grace-seconds',type=float,default=4.0)
    p.add_argument('--display-config',type=Path,default=None,
                   help='TOML with optional [display] SPI/DC/RESET pins '
                        '(default: player.toml in the standard config directory)')
    p.add_argument('--spi-hz',type=int,default=None)
    p.add_argument('--contrast',type=int,default=None)
    p.add_argument('--oled-recover-on-error',action='store_true',default=None,
                   help='opt-in: one full reinitialization after a reported SPI OSError')
    p.add_argument('--ambient-auto',action='store_true',default=None,help='Enable independent APDS9306 OLED contrast control')
    p.add_argument('--ambient-socket',default='/run/apds9306.sock')
    p.add_argument('--ambient-timeout-ms',type=int,default=150)
    p.add_argument('--ambient-poll-ms',type=int,default=1000)
    p.add_argument('--ambient-stale-ms',type=int,default=5000)
    p.add_argument('--ambient-smoothing-ms',type=int,default=2000)
    p.add_argument('--ambient-fallback-contrast',type=int,default=128)
    args=p.parse_args(argv)
    from sonoscdplayer.ui.config import load_ui_config
    try:
        ui_config = load_ui_config(args.ui_config)
    except (ValueError, OSError) as exc:
        p.error('invalid UI configuration: '+str(exc))
    from sonoscdplayer.ui.cover_state import read_cover_mode
    saved_cover, _ = read_cover_mode(args.state_dir)
    cover_mode = (args.cover if args.cover is not None else
                  saved_cover if saved_cover is not None else ui_config.cover_mode)
    from sonoscdplayer.ui.i18n import available_languages
    from sonoscdplayer.ui.language_state import UiState
    ui_state = UiState(args.state_dir)
    # A language chosen in the menu (state file) wins over ui.toml.
    language = ui_state.language(available_languages()) or ui_config.language
    if args.gpio != args.execute: p.error('--gpio and --execute must be given together')
    try:
        from sonoscdplayer.system import config_paths
        display_path = args.display_config or config_paths.player_config()
        if args.display_config is None and not display_path.exists() and not args.execute:
            display_pins = DisplayPins()  # offline preview without a configured device
        else:
            display_pins = load_display_pins(display_path)
        from sonoscdplayer.system.gpio_settings import gpio_warnings_enabled
        gpio_warnings = gpio_warnings_enabled(display_path) if args.execute else False
    except (OSError, ValueError) as exc:
        p.error(f'invalid display configuration: {exc}')
    overrides = {key: value for key, value in dict(
        spi_hz=args.spi_hz, contrast=args.contrast, ambient_auto=args.ambient_auto,
        adopt_boot_splash=args.adopt_boot_splash,
        preserve_on_exit=args.preserve_display_on_exit,
        recover_on_error=args.oled_recover_on_error).items() if value is not None}
    try:
        display_pins = replace(display_pins, **overrides)
    except ValueError as exc:
        p.error(str(exc))
    backend = backend_for(display_pins.controller)
    args.spi_hz, args.contrast = display_pins.spi_hz, display_pins.contrast
    args.ambient_auto = display_pins.ambient_auto
    args.adopt_boot_splash = display_pins.adopt_boot_splash
    args.preserve_display_on_exit = display_pins.preserve_on_exit
    args.oled_recover_on_error = display_pins.recover_on_error
    converter = partial(image_to_frame, color=True) if backend.color else image_to_frame
    render_status = (partial(selected_status_frame, color=True) if backend.color
                     else selected_status_frame)
    render_notice = partial(notice_frame, color=True) if backend.color else notice_frame
    def render_menu():
        return menu.frame(converter, gray=False) if backend.color else menu.frame(converter)
    if args.ui_target_rpc and not (args.ui_events_socket and args.ui_modern):
        p.error('--ui-target-rpc requires --ui-modern and --ui-events-socket')
    if args.ui_power_actions and not (args.ui_events_socket and args.ui_modern):
        p.error('--ui-power-actions requires --ui-modern and --ui-events-socket')
    if args.ui_events_socket and not args.ui_modern:
        p.error('--ui-events-socket requires --ui-modern')
    try:
        ambient_config=OLEDAmbientConfig(socket_path=args.ambient_socket,
            timeout_ms=args.ambient_timeout_ms,poll_ms=args.ambient_poll_ms,
            stale_ms=args.ambient_stale_ms,smoothing_ms=args.ambient_smoothing_ms,
            fallback_contrast=args.ambient_fallback_contrast)
    except ValueError as exc: p.error(str(exc))
    if ((args.splash_seconds is not None and (not math.isfinite(args.splash_seconds) or not 0 <= args.splash_seconds <= 30))
            or not math.isfinite(args.splash_ready_timeout) or not 0 < args.splash_ready_timeout <= 300
            or not math.isfinite(args.seconds) or args.seconds < 0 or args.seconds > 3600):
        p.error('invalid duration')
    if not 200 <= args.refresh_ms <= 10000: p.error('refresh-ms must be 200..10000')
    if not 0 <= args.offline_grace_seconds <= 30: p.error('offline grace must be 0..30 seconds')
    driver = backend.create(display_pins, gpio_warnings=gpio_warnings) if args.execute else None
    splash = start_splash(args.splash, language, color=backend.color)
    ambient=OLEDContrastControl(ambient_config) if args.ambient_auto else None
    last_contrast=args.contrast
    recovery = None
    last_frame = None
    if args.oled_recover_on_error and args.execute:
        from sonoscdplayer.hardware.displays.recovery import RecoveryBudget
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
        from sonoscdplayer.ui.menu.interactive_menu import InteractiveMenu
        from sonoscdplayer.ui.menu.ui_event_bridge import EventReceiver
        if args.ui_target_rpc:
            from sonoscdplayer.ui.menu.speakers.target_rpc_client import submit_target
            sender = lambda uid: submit_target(uid, socket_path=args.socket)
            group_sender = lambda uid, members: submit_target(
                uid, members=members, socket_path=args.socket)
            from sonoscdplayer.ui.menu.speakers.target_rpc_client import read_target, reset_disc
            current_target = lambda: read_target(socket_path=args.socket)
            disc_reset = lambda: reset_disc(socket_path=args.socket)
        else:
            sender = None
            group_sender = None
            current_target = None
            disc_reset = None
        power_action = None
        if args.ui_power_actions:
            from sonoscdplayer.system.power_action import PowerRequester
            power_action = PowerRequester().request
        network_setup = None
        if args.ui_network_setup:
            from sonoscdplayer.network_setup.nm import Nm
            from sonoscdplayer.network_setup.session import SetupSession
            from sonoscdplayer.network_setup.web import SetupWebServer
            from sonoscdplayer.ui.i18n import Translator
            # The page follows the current menu language.
            network_setup = SetupSession(Nm(), web_factory=lambda session, address: SetupWebServer(
                session, address, lambda: Translator(menu.language)))
        menu = InteractiveMenu(language=language, target_rpc=sender,
                               group_target_rpc=group_sender, power_action=power_action,
                               current_target=current_target,
                               on_language_change=ui_state.save_language,
                               network_setup=network_setup,
                               text_extra_characters=ui_config.extra_characters,
                               disc_reset=disc_reset)
        receiver_context = EventReceiver(args.ui_events_socket)
    def stop_on_sigterm(_signum, _frame):
        # systemd's normal SIGTERM must run finally and release SPI/GPIO.
        raise SystemExit(0)

    previous_sigterm = signal.signal(signal.SIGTERM, stop_on_sigterm)
    try:
        if driver: driver.open(adopt_existing=args.adopt_boot_splash)
        end=time.monotonic()+args.seconds if args.seconds else None
        previous=None
        previous_label=None
        next_language_check_at=0.0
        next_status_at=0.0
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
            ready_state=wait_for_player_ready(
                request_status, args.socket,
                max_wait_seconds=player_ready_timeout(
                    args.splash_ready_timeout,
                    preserve=preserve_display_on_exit(args.preserve_display_on_exit)))
        if ready_state is not None:
            hold.success(ready_state,time.monotonic())
        with receiver_context as ui_events:
            while end is None or time.monotonic()<end:
                if menu is not None and time.monotonic() >= next_language_check_at:
                    # Never interpret a state-file change as a menu button press.
                    # Poll at most once a second; no player or drive RPC.
                    next_language_check_at = time.monotonic() + 1.0
                    refresh_saved_language(menu, ui_state)
                menu_quick = False
                if menu is not None:
                    menu.poll()
                    handled = False
                    for _ in range(16):
                        received = ui_events.receive()
                        if received is None:
                            break
                        handled = menu.event(*received) or handled
                    menu_quick = (getattr(menu, 'active', False) is True
                                  or getattr(menu, 'power_menu', False) is True)
                    if handled and menu_quick:
                        # Show the menu's answer to the turn/press at once; the
                        # status request below can take up to its timeout while
                        # the player is busy (e.g. waking the drive).
                        try:
                            # Same priorities as below (drive notices first),
                            # with the last known player status.
                            quick=(render_status(
                                       hold.last_state, menu=menu, ui_modern=args.ui_modern,
                                       cover_mode=cover_mode, language=menu.language)
                                   if hold.last_state is not None else render_menu())
                        except (ValueError, TypeError, KeyError):
                            quick=render_menu()
                        if bytes(quick.data)!=previous:
                            write_frame(quick)
                            previous=bytes(quick.data)
                if ambient is not None:
                    new_contrast=ambient.tick(time.monotonic()*1000)
                    if new_contrast != last_contrast:
                        adjust_contrast(new_contrast)
                        print(json.dumps({'oled_contrast':new_contrast,'mode':'ambient','hardware':args.execute}),flush=True)
                        last_contrast=new_contrast
                try:
                    if ready_state is not None:
                        state=hold.success(ready_state, time.monotonic())
                    elif (menu_quick and hold.last_state is not None
                          and time.monotonic() < next_status_at):
                        # Menu open: answer the encoder quickly, but ask the
                        # player only every --refresh-ms.
                        state=hold.last_state
                    else:
                        next_status_at=time.monotonic()+args.refresh_ms/1000
                        state=hold.success(request_status(args.socket), time.monotonic())
                    ready_state=None
                    if menu is not None and hasattr(menu, 'player_status'):
                        menu.player_status(state)
                    elif menu is not None and hasattr(menu, 'player_mode'):
                        menu.player_mode = (state.get('player') or {}).get('mode')
                    frame=render_status(
                        state, menu=menu, ui_modern=args.ui_modern,
                        cover_mode=cover_mode,
                        language=(menu.language if menu is not None else language))
                    label={'screen':'status','mode':(state.get('player') or {}).get('mode'),'hardware':args.execute}
                except (OSError,ValueError,TimeoutError,TypeError) as exc:
                    # The player_service socket is single-threaded. While PLAY waits
                    # for initial PCM, status requests can queue for up to 45s.
                    # Keep a previously validated screen temporarily on socket
                    # congestion, but not on missing/refused/unauthorized service.
                    busy = isinstance(exc, (BlockingIOError, TimeoutError, socket.timeout))
                    state=hold.failure(time.monotonic(), 50.0 if busy else None)
                    # A rendering/validation exception must never be fed
                    # back through the same renderer via StatusHold: the
                    # previously cached state may itself be invalid.
                    if busy and state is not None:
                        try:
                            frame=render_status(
                                state, menu=menu, ui_modern=args.ui_modern,
                                cover_mode=cover_mode, cached=True,
                                language=(menu.language if menu is not None else language))
                            label={'screen':'status_cached','mode':(state.get('player') or {}).get('mode'),'hardware':args.execute}
                        except (ValueError, TypeError, KeyError):
                            frame=render_notice('status.invalid_player_status', severity='error',
                                               language=(menu.language if menu is not None else language))
                            label={'screen':'status_invalid','hardware':args.execute}
                    else:
                        # Menu input is still processed while the player service is
                        # down, so an open menu must stay visible instead of being
                        # replaced by the error screen (it is the Wi-Fi fallback).
                        frame=(render_menu() if menu is not None and menu.active
                               else render_notice('status.player_offline', severity='error',
                                                 language=(menu.language if menu is not None else language)))
                        label={'screen':'status_unavailable','error':type(exc).__name__,'hardware':args.execute}
                current=bytes(frame.data)
                if current!=previous:
                    write_frame(frame)
                    # Scrolling text redraws several times a second; log
                    # only when the kind of screen or the mode changes.
                    if label!=previous_label:
                        print(json.dumps(label,ensure_ascii=False),flush=True)
                        previous_label=label
                    previous=current
                delay=MENU_FRAME_SECONDS if menu_quick else frame_delay(args.refresh_ms/1000)
                time.sleep(delay if end is None else min(delay,max(0,end-time.monotonic())))
    finally:
        try:
            if driver:
                preserve = preserve_display_for_shutdown(args.preserve_display_on_exit)
                print(json.dumps({
                    'oled_exit': 'close',
                    'shutdown_marker_present': SHUTDOWN_PRESERVE_MARKER.is_file(),
                    'preserve': preserve,
                    'blank': not preserve,
                }), flush=True)
                driver.close(blank=not preserve)
        finally:
            signal.signal(signal.SIGTERM, previous_sigterm)

if __name__=='__main__': main()
