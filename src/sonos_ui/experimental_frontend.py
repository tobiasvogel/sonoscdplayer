#!/usr/bin/env python3
"""Experimental checkbox UI: existing buttons -> private event socket -> OLED.

No GPIO owner, no Sonos commands, no group mutations. This replaces ONLY the
OLED service during explicit hardware tests. Default renders a PNG offscreen.
"""
import argparse
from pathlib import Path
import signal
import time

from .multi_target import MultiSelection, discover_targets
from .multi_render import MultiRenderer
from .ui_event_bridge import EventReceiver

DEFAULT_EVENTS = '/run/sonoscdplayer/ui-events.sock'


def action_for(name, event):
    return {('encoder', 'right'): 'next',
            ('encoder', 'left'): 'previous',
            ('encoder_sw', 'pressed'): 'confirm',
            ('connect', 'pressed'): 'back'}.get((name, event))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--events', default=DEFAULT_EVENTS)
    parser.add_argument('--timeout', type=float, default=3.0)
    parser.add_argument('--seconds', type=float, default=30.0)
    parser.add_argument('--refresh', type=float, default=15.0)
    parser.add_argument('--execute-oled', action='store_true')
    parser.add_argument('--output', type=Path, default=Path('/tmp/m016b-live-ui.png'))
    args = parser.parse_args(argv)
    if not 0 < args.seconds <= 120 or not 0 < args.timeout <= 10 or not 2 <= args.refresh <= 60:
        parser.error('invalid timeout, refresh or duration')
    state = MultiSelection().update(discover_targets(timeout=args.timeout))
    renderer = MultiRenderer()
    stop = False
    def terminate(*_):
        nonlocal stop
        stop = True
    signal.signal(signal.SIGINT, terminate)
    signal.signal(signal.SIGTERM, terminate)
    driver = None
    if args.execute_oled:
        from integrations.outputs.ssd1327 import SSD1327, DisplayConfig
        from integrations.outputs.oled_status import image_to_frame
        driver = SSD1327(DisplayConfig())
    try:
        with EventReceiver(args.events) as events:
            if driver is not None:
                driver.open(adopt_existing=False)
            end = time.monotonic() + args.seconds
            refresh_at = time.monotonic() + args.refresh
            prior = None
            dirty = True
            while not stop and time.monotonic() < end:
                for _ in range(16):
                    msg = events.receive()
                    if msg is None:
                        break
                    action = action_for(*msg)
                    if action:
                        state = state.action(action)
                        dirty = True
                now = time.monotonic()
                if now >= refresh_at:
                    state = state.update(discover_targets(timeout=args.timeout))
                    dirty = True
                    refresh_at = time.monotonic() + args.refresh
                if dirty:
                    image = renderer.render_multi(state, gray=True)
                    if driver is None:
                        args.output.parent.mkdir(parents=True, exist_ok=True)
                        image.save(args.output)
                    else:
                        frame = image_to_frame(image)
                        pixels = bytes(frame.data)
                        if pixels != prior:
                            driver.show(frame)
                            prior = pixels
                    dirty = False
                time.sleep(0.03)
    finally:
        if driver is not None:
            driver.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
