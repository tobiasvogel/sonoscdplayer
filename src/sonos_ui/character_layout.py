"""ARC-DISPLAY-01B: pure character-cell layout for semantic screens.

Layout is deterministic and read-only. Encoder events, menu intents and
authorization remain owned by the existing UI/controller code. No Pillow,
GPIO, Sonos, backend access or network calls.
"""
from __future__ import annotations

import unicodedata

from .display_backend import CharacterFrame, validate_frame
from .display_capabilities import DisplayCapabilities
from .semantic_screen import SemanticScreen, ScreenItem

# Portable ASCII fallback. A future concrete driver may implement its own
# native CGROM mapping; never emit unspecified Unicode glyphs to the backend.
_REPLACEMENTS = {"ä": "ae", "ö": "oe", "ü": "ue", "Ä": "Ae",
                 "Ö": "Oe", "Ü": "Ue", "ß": "ss", "€": "EUR",
                 "→": ">", "←": "<", "✓": "*", "✕": "x",
                 "⌫": "<", "␣": "_", "…": "...", "–": "-", "—": "-"}


def portable_text(value: str) -> str:
    """Map Unicode to visible ASCII, never transmit control characters."""
    if not isinstance(value, str):
        raise TypeError("expected text")
    out = []
    for char in value:
        if char in _REPLACEMENTS:
            out.append(_REPLACEMENTS[char])
        elif char in "\n\r\t" or unicodedata.category(char).startswith("C"):
            out.append(" ")
        elif 32 <= ord(char) <= 126:
            out.append(char)
        else:
            folded = unicodedata.normalize("NFKD", char)
            ascii_part = "".join(c for c in folded if 32 <= ord(c) <= 126)
            out.append(ascii_part or "?")
    return "".join(out)


def _fit(text: str, width: int, *, offset: int = 0) -> str:
    """Render a horizontal window. Offset is a cell offset, not elapsed time."""
    value = portable_text(text)
    if type(offset) is not int or offset < 0:
        raise ValueError("offset must be a nonnegative integer")
    if len(value) <= width:
        return value.ljust(width)
    # A caller can cycle offset based on focus age; no hidden mutable timers.
    start = min(offset, len(value) - width)
    return value[start:start + width].ljust(width)


def _page_lines(message: str, width: int) -> list[str]:
    """Word-aware wrap, preserving deliberate newlines and long tokens."""
    result = []
    for raw in message.split("\n"):
        raw = portable_text(raw)
        if not raw:
            result.append("")
            continue
        words = raw.split(" ")
        current = ""
        for word in words:
            if not word:
                continue
            while len(word) > width:
                if current:
                    result.append(current)
                    current = ""
                result.append(word[:width])
                word = word[width:]
            if word:
                trial = word if not current else current + " " + word
                if len(trial) > width:
                    result.append(current)
                    current = word
                else:
                    current = trial
        result.append(current)
    return result or [""]


def _menu_line(item: ScreenItem, width: int, *, offset: int) -> str:
    # Distinct markers preserve independent focus and checkbox selection.
    # '>*' = focused and selected, '> ' = focused only, ' *' = selected.
    prefix = (">" if item.focused else " ") + ("*" if item.selected else " ")
    label = item.label if item.value is None else f"{item.label}: {item.value}"
    if not item.enabled:
        prefix = ("!" if item.focused else "-") + prefix[1]
    return prefix + _fit(label, width - 2, offset=offset)


def _menu_rows(screen: SemanticScreen, width: int, height: int,
               *, offset: int) -> tuple[str, ...]:
    items = screen.items
    if not items:
        return tuple([_fit(screen.heading, width)] + [" " * width] * (height - 1))
    # 16x2: both rows are actionable; 20x4: title + three actionable rows.
    heading_rows = 1 if height >= 3 and screen.heading else 0
    capacity = height - heading_rows
    focus = next((i for i, item in enumerate(items) if item.focused), 0)
    start = max(0, min(focus - capacity + 1, len(items) - capacity))
    visible = items[start:start + capacity]
    lines = [_fit(screen.heading, width)] if heading_rows else []
    lines += [_menu_line(item, width, offset=offset if item.focused else 0)
              for item in visible]
    return tuple((lines + [" " * width] * height)[:height])


def _info_rows(screen: SemanticScreen, width: int, height: int, *,
               page: int, offset: int) -> tuple[str, ...]:
    fields = [f"{item.label}: {item.value}" if item.value is not None else item.label
              for item in screen.items]
    if not fields:
        fields = [screen.heading or screen.kind]
    # Every field is reachable via a page parameter; no silent loss of fields.
    if type(page) is not int or page < 0:
        raise ValueError("page must be nonnegative")
    start = min(page * height, max(0, len(fields) - 1))
    lines = [_fit(value, width, offset=offset) for value in fields[start:start + height]]
    return tuple((lines + [" " * width] * height)[:height])


def _notice_rows(screen: SemanticScreen, width: int, height: int, *,
                 page: int) -> tuple[str, ...]:
    lines = _page_lines(screen.message or "", width)
    if type(page) is not int or page < 0:
        raise ValueError("page must be nonnegative")
    start = min(page * height, max(0, len(lines) - height))
    visible = lines[start:start + height]
    return tuple(([_fit(line, width) for line in visible]
                  + [" " * width] * height)[:height])



def character_page_count(screen: SemanticScreen,
                         capabilities: DisplayCapabilities) -> int:
    """Count non-menu pages for a caller-driven, read-only carousel.

    Menu pages are driven by the controller's focus, not a second navigation
    state. A notice may use automatic page cycling because it consumes normal
    encoder input under the existing NoticeTiming policy.
    """
    if not isinstance(screen, SemanticScreen):
        raise TypeError("expected SemanticScreen")
    if not isinstance(capabilities, DisplayCapabilities) or capabilities.kind != "character":
        raise ValueError("character capabilities required")
    if screen.kind == "menu":
        return 1
    if screen.kind == "notice":
        lines = _page_lines(screen.message or "", capabilities.columns)
    else:
        lines = [item for item in screen.items] or [screen.heading]
    return max(1, (len(lines) + capabilities.rows - 1) // capabilities.rows)

def render_character_screen(screen: SemanticScreen,
                            capabilities: DisplayCapabilities,
                            *, page: int = 0, scroll_offset: int = 0) -> CharacterFrame:
    """Convert an immutable screen to a validated, exact-size character frame.

    page is for read-only notice and metadata pagination. Menu focus is owned
    by the existing controller, never advanced or confirmed by this renderer.
    """
    if not isinstance(screen, SemanticScreen):
        raise TypeError("expected SemanticScreen")
    if not isinstance(capabilities, DisplayCapabilities) or capabilities.kind != "character":
        raise ValueError("a character display is required")
    width, height = capabilities.columns, capabilities.rows
    if width < 3:
        raise ValueError("character menu needs at least three columns")
    if type(scroll_offset) is not int or scroll_offset < 0:
        raise ValueError("scroll_offset must be nonnegative")
    if type(page) is not int or page < 0:
        raise ValueError("page must be nonnegative")
    if screen.kind == "menu":
        rows = _menu_rows(screen, width, height, offset=scroll_offset)
    elif screen.kind == "notice":
        rows = _notice_rows(screen, width, height, page=page)
    else:
        rows = _info_rows(screen, width, height, page=page, offset=scroll_offset)
    frame = CharacterFrame(rows)
    validate_frame(capabilities, frame)
    return frame
