"""160x128 color projection of semantic screens; rendering only, no actions."""
from __future__ import annotations

import math
from PIL import Image, ImageDraw

from sonoscdplayer.ui.menu.marquee import Marquee
from sonoscdplayer.ui.renderers.oled_128 import font
from sonoscdplayer.ui.screens.semantic_screen import SemanticScreen
from sonoscdplayer.ui.renderers.theme import MODERN_HIFI

SIZE = (160, 128)
MENU_TOP, MENU_STEP, MENU_HEIGHT, MENU_ROWS = 34, 23, 20, 3


def menu_window(screen):
    """Indexes of the visible menu rows; the focused row stays visible."""
    focus = next((i for i, item in enumerate(screen.items) if item.focused), None)
    if screen.items and focus is None:
        raise ValueError('landscape menu needs a focused item')
    start = max(0, min((focus or 0)-1, len(screen.items)-MENU_ROWS))
    return range(start, min(len(screen.items), start+MENU_ROWS))


class LandscapeRenderer:
    """Draws the common screens on a 160x128 color display (ST7735S)."""
    def __init__(self, *, theme=MODERN_HIFI, font_loader=font):
        self.theme, self.font_loader = theme, font_loader
        self.marquee = Marquee()

    def _text(self, canvas, box, value, *, size=11, bold=False,
              fill=None, background=None, scroll=False, elapsed=0):
        x, y, width, height = box
        if not (0 <= x < 160 and 0 <= y < 128 and 0 < width <= 160-x
                and 0 < height <= 128-y):
            raise ValueError('text viewport outside landscape canvas')
        if not value:
            return
        layer = Image.new('RGB', (width, height), background or self.theme.background)
        draw = ImageDraw.Draw(layer)
        face = self.font_loader(size, bold)
        value = str(value)
        left, top, right, bottom = draw.textbbox((0, 0), value, font=face)
        offset = self.marquee.offset(right-left, width, elapsed) if scroll else 0
        draw.text((-left-offset, max(0, (height-(bottom-top))//2)-top), value,
                  font=face, fill=fill or self.theme.primary)
        canvas.paste(layer, (x, y))

    @staticmethod
    def _time(value):
        minutes, seconds = divmod(value, 60)
        return f'{minutes}:{seconds:02d}'

    def render(self, screen, *, elapsed=0):
        """The screen as RGB image; ``elapsed`` drives text scrolling."""
        if not isinstance(screen, SemanticScreen):
            raise TypeError('expected SemanticScreen')
        if not isinstance(elapsed, (int, float)) or not math.isfinite(elapsed) or elapsed < 0:
            raise ValueError('animation time must be finite and nonnegative')
        t = self.theme
        canvas = Image.new('RGB', SIZE, t.background)
        draw = ImageDraw.Draw(canvas)
        text = lambda box, value, **kw: self._text(canvas, box, value, elapsed=elapsed, **kw)
        if screen.kind == 'menu':
            text((4, 1, 152, 15), screen.heading, size=12, bold=True)
            draw.line((0, 18, 159, 18), fill=t.muted)
            text((4, 20, 152, 11), screen.subheading, size=9, fill=t.secondary)
            if not screen.items:
                text((4, 48, 152, 18), screen.placeholder, scroll=True)
            for slot, index in enumerate(menu_window(screen)):
                item = screen.items[index]
                y = MENU_TOP+slot*MENU_STEP
                background = t.accent if item.focused else t.background
                foreground = t.selection_text if item.focused else t.primary
                if item.focused:
                    draw.rectangle((2, y, 157, y+MENU_HEIGHT-1), fill=background)
                text((6, y, 10, MENU_HEIGHT), '>' if item.focused else '',
                     fill=foreground, background=background)
                text((20, y, 118, MENU_HEIGHT), item.label, fill=foreground,
                     background=background, scroll=item.focused)
                if item.intent != 'back':
                    draw.rectangle((143, y+5, 152, y+14), outline=foreground)
                    if item.selected:
                        draw.rectangle((146, y+8, 149, y+11), fill=foreground)
            draw.line((0, 108, 159, 108), fill=t.muted)
            text((4, 112, 122, 12), screen.message or screen.status or screen.footer,
                 size=9, fill=t.secondary, scroll=True)
            if screen.items:
                focus = next(i for i, item in enumerate(screen.items) if item.focused)
                text((130, 112, 26, 12), f'{focus+1}/{len(screen.items)}', size=9, fill=t.accent)
        elif screen.kind in ('playback', 'idle'):
            values = {item.key: item.value for item in screen.items}
            state = values.get('playback', 'stopped')
            label = {'playing': 'PLAY', 'paused': 'PAUSE', 'stopped': 'STOP'}.get(state, state)
            text((4, 1, 104, 15), 'CD '+(values.get('track') or '--'), size=12, bold=True)
            text((112, 1, 44, 15), label, size=11, bold=True,
                 fill=t.playing if state == 'playing' else t.secondary)
            draw.line((0, 18, 159, 18), fill=t.muted)
            text((4, 23, 152, 12), values.get('album'), size=10, fill=t.secondary, scroll=True)
            text((4, 41, 152, 18), values.get('track_title') or values.get('album') or screen.heading,
                 size=14, bold=True, scroll=True)
            text((4, 64, 152, 14), values.get('artist'), fill=t.secondary, scroll=True)
            text((4, 85, 152, 13), values.get('speaker'), size=10, fill=t.accent, scroll=True)
            current, total = values.get('elapsed_seconds'), values.get('duration_seconds')
            if current is not None and total is not None:
                current, total = int(current), int(total)
                if current < 0 or total < 0:
                    raise ValueError('playback times cannot be negative')
                draw.rectangle((4, 105, 155, 109), fill=t.surface)
                filled = round(152*min(current, total)/total) if total else 0
                if filled:
                    draw.rectangle((4, 105, 3+filled, 109), fill=t.accent)
                text((4, 114, 152, 12), f'{self._time(current)} / {self._time(total)}',
                     size=10, fill=t.secondary)
        elif screen.kind == 'notice':
            text((4, 1, 152, 16), screen.heading or screen.severity.upper(), bold=True, fill=t.warning)
            draw.line((0, 18, 159, 18), fill=t.muted)
            text((4, 48, 152, 20), screen.message, size=12, scroll=True)
            text((4, 112, 152, 12), screen.footer, size=9, fill=t.secondary)
        return canvas
