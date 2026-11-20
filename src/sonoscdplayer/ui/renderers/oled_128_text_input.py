"""128x128 drawing of a ``TextEntry`` with the shared renderer helpers."""
from PIL import Image, ImageDraw

from sonoscdplayer.ui.renderers.oled_128 import font
from sonoscdplayer.ui.menu.text_input import ACTIONS, CANCEL, DELETE, DONE
from sonoscdplayer.ui.renderers.theme import ssd1327_gray

ACTION_SYMBOL = {DONE: '✓', DELETE: '⌫', CANCEL: '✕'}
ACTION_LABEL = {DONE: 'textinput.done', DELETE: 'textinput.delete',
                CANCEL: 'textinput.cancel'}
SPACE_SYMBOL = '␣'
ENTRY_BOX = (4, 30, 123, 47)
WHEEL_TOP = 60
NEIGHBOUR_X = (6, 27, 92, 113)   # two left, two right of the centre box
CENTRE_BOX = (48, WHEEL_TOP, 79, WHEEL_TOP + 31)


def shown(char):
    """How a character is drawn in the character wheel (symbols for space and actions)."""
    if char in ACTION_SYMBOL:
        return ACTION_SYMBOL[char]
    return SPACE_SYMBOL if char == ' ' else char


def visible_tail(text, width, size):
    """The end of ``text`` that fits ``width``; spaces made visible."""
    value = ''.join(shown(c) for c in text)
    f = font(size)
    measure = ImageDraw.Draw(Image.new('L', (1, 1)))
    while value and measure.textlength(value, font=f) > width:
        value = value[1:]
    return value, measure.textlength(value, font=f)


def render_text_entry(renderer, entry, *, title, subtitle='', gray=False):
    """Title, optional subtitle, the text so far, and the character wheel."""
    t = renderer.theme
    tr = renderer.tr
    image = Image.new('RGB', (128, 128), t.background)
    draw = ImageDraw.Draw(image)
    renderer.shell(image, title)
    if subtitle:
        renderer.text(image, (5, 17), subtitle, fill=t.secondary, size=8, width=118,
                      height=11, scroll=True)

    # Text so far, right-aligned tail with a cursor bar behind it.
    draw.rounded_rectangle(ENTRY_BOX, radius=3, fill=t.surface, outline=t.muted)
    tail, width = visible_tail(entry.text, 106, 11)
    if tail:
        renderer.text(image, (8, 32), tail, fill=t.primary, size=11, width=110, height=14)
    cursor_x = min(118, 9 + int(width))
    draw.line((cursor_x, 33, cursor_x, 44), fill=t.accent, width=1)

    # Error or byte count under the box.
    if entry.error:
        key, params = entry.error
        renderer.text(image, (5, 49), tr(key, **params), fill=t.warning, size=8,
                      width=118, height=10, scroll=True)
    else:
        count = f'{entry.byte_length}/{entry.max_bytes}'
        renderer.text(image, (80, 49), count, fill=t.secondary, size=8, width=43,
                      height=10)

    # Character wheel: neighbours small, current character large and framed.
    for x, offset in zip(NEIGHBOUR_X, (-2, -1, 1, 2)):
        char = entry.neighbour(offset)
        fill = t.secondary if abs(offset) == 1 else t.muted
        renderer.text(image, (x, WHEEL_TOP + 9), shown(char), fill=fill, size=11,
                      width=14, height=14)
    draw.rounded_rectangle(CENTRE_BOX, radius=4, fill=t.accent)
    current = entry.current
    big = font(20, True)
    label = shown(current)
    box = draw.textbbox((0, 0), label, font=big)
    x = (CENTRE_BOX[0] + CENTRE_BOX[2] + 1 - (box[2] - box[0])) // 2 - box[0]
    y = (CENTRE_BOX[1] + CENTRE_BOX[3] + 1 - (box[3] - box[1])) // 2 - box[1]
    draw.text((x, y), label, font=big, fill=t.selection_text)

    if current in ACTIONS:
        status = tr(ACTION_LABEL[current])
    elif current == ' ':
        status = tr('textinput.space')
    else:
        status = tr('textinput.connect_hint')
    renderer.bottom(image, status=status, footer=tr('textinput.footer'))
    return ssd1327_gray(image) if gray else image
