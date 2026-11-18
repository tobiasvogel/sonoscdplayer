"""Bounded monochrome 128x64 projection of existing semantic screens.

This renderer only draws data. It does not navigate menus or execute intents.
"""
from __future__ import annotations

import math
from PIL import Image, ImageDraw

from .marquee import Marquee
from .model import DisplayModel
from .render import font
from .semantic_screen import SemanticScreen, ScreenItem, screen_from_display_model

SIZE = (128, 64)
MENU_TOP, MENU_STEP, MENU_HEIGHT, MENU_ROWS = 14, 12, 12, 3


def compact_screen_from_model(model: DisplayModel) -> SemanticScreen:
    """Extend the common bridge with optional playback times, in seconds."""
    from dataclasses import replace
    screen = screen_from_display_model(model)
    if model.view not in ('idle', 'playback') or not model.disc_present:
        return screen
    times = []
    for key, value in (('elapsed_seconds', model.elapsed_seconds),
                       ('duration_seconds', model.duration_seconds)):
        if value is not None:
            if type(value) is not int or value < 0:
                raise ValueError('playback time must be a nonnegative integer')
            times.append(ScreenItem(key, key, value=str(value)))
    return replace(screen, items=screen.items + tuple(times))


def menu_window(screen):
    """Three rows with focus visible; selected and focused remain independent."""
    focused = [i for i, item in enumerate(screen.items) if item.focused]
    if screen.items and not focused:
        raise ValueError('compact menu needs a focused item')
    focus = focused[0] if focused else 0
    start = max(0, min(focus - 1, len(screen.items) - MENU_ROWS))
    return range(start, min(len(screen.items), start + MENU_ROWS))


class CompactRenderer:
    def __init__(self, *, font_loader=font):
        self.font_loader = font_loader
        self.marquee = Marquee()

    def _text(self, canvas, box, value, *, size=10, bold=False,
              inverted=False, scroll=False, elapsed=0):
        x, y, width, height = box
        if not (0 <= x < 128 and 0 <= y < 64 and 0 < width <= 128-x
                and 0 < height <= 64-y):
            raise ValueError('text viewport outside compact canvas')
        if not value:
            return
        face = self.font_loader(size, bold)
        layer = Image.new('1', (width, height), int(inverted))
        draw = ImageDraw.Draw(layer)
        value = str(value)
        left, top, right, bottom = draw.textbbox((0, 0), value, font=face)
        ink_width = right - left
        offset = self.marquee.offset(ink_width, width, elapsed) if scroll else 0
        draw.text((-left-offset, max(0, (height-(bottom-top))//2)-top),
                  value, font=face, fill=int(not inverted))
        canvas.paste(layer, (x, y))

    @staticmethod
    def _time(value):
        minutes, seconds = divmod(value, 60)
        return f'{minutes}:{seconds:02d}'

    def render(self, screen: SemanticScreen, *, elapsed=0):
        if not isinstance(screen, SemanticScreen):
            raise TypeError('expected SemanticScreen')
        if not isinstance(elapsed, (int, float)) or not math.isfinite(elapsed) or elapsed < 0:
            raise ValueError('animation time must be finite and nonnegative')
        canvas = Image.new('1', SIZE)
        draw = ImageDraw.Draw(canvas)
        text = lambda box, value, **kw: self._text(canvas, box, value, elapsed=elapsed, **kw)
        if screen.kind == 'menu':
            text((2, 0, 124, 11), screen.heading, bold=True)
            draw.line((0, 11, 127, 11), fill=1)
            indices = menu_window(screen)
            if not screen.items:
                text((2, 25, 124, 12), screen.placeholder, scroll=True)
            for slot, index in enumerate(indices):
                item = screen.items[index]
                y = MENU_TOP + slot * MENU_STEP
                if item.focused:
                    draw.rectangle((0, y, 127, y+MENU_HEIGHT-1), fill=1)
                text((1, y, 10, MENU_HEIGHT), '>' if item.focused else '',
                     inverted=item.focused)
                text((12, y, 103, MENU_HEIGHT), item.label,
                     inverted=item.focused, scroll=item.focused)
                # Fixed selection indicator at right, independent of focus.
                draw.rectangle((119, y+3, 125, y+9), outline=int(not item.focused))
                if item.selected:
                    draw.rectangle((121, y+5, 123, y+7), fill=int(not item.focused))
            if screen.items:
                focus = next(i for i, item in enumerate(screen.items) if item.focused)
                text((99, 52, 28, 11), f'{focus+1}/{len(screen.items)}', size=9)
            text((1, 52, 96, 11), screen.message or screen.status or screen.footer,
                 size=9, scroll=True)
        elif screen.kind in ('playback', 'idle'):
            values = {item.key: item.value for item in screen.items}
            state = values.get('playback', 'stopped')
            state = {'playing': 'PLAY', 'paused': 'PAUSE', 'stopped': 'STOP'}.get(state, state)
            text((2, 0, 79, 11), 'CD ' + (values.get('track') or '--'), bold=True)
            text((83, 0, 43, 11), state, bold=True)
            draw.line((0, 11, 127, 11), fill=1)
            text((2, 13, 124, 12), values.get('track_title') or values.get('album') or screen.heading,
                 size=11, bold=True, scroll=True)
            text((2, 26, 124, 10), values.get('artist'), scroll=True)
            text((2, 38, 124, 10), values.get('speaker'), size=9, scroll=True)
            current, total = values.get('elapsed_seconds'), values.get('duration_seconds')
            if current is not None and total is not None:
                current, total = int(current), int(total)
                if current < 0 or total < 0:
                    raise ValueError('playback times cannot be negative')
                text((2, 49, 124, 10), f'{self._time(current)} / {self._time(total)}', size=9)
                draw.rectangle((1, 61, 126, 63), outline=1)
                filled = round(124 * min(current, total) / total) if total > 0 else 0
                if filled:
                    draw.line((2, 62, 1+filled, 62), fill=1)
        elif screen.kind == 'notice':
            text((2, 0, 124, 11), screen.heading or screen.severity.upper(), bold=True)
            draw.line((0, 11, 127, 11), fill=1)
            text((2, 22, 124, 15), screen.message, scroll=True)
            text((2, 50, 124, 12), screen.footer, size=9)
        return canvas
