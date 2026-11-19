"""Semantic screens shared by graphical and text layouts.

No GPIO, Pillow, player RPC or hardware-specific coordinates are used here.
An action is a stable UI intent, never permission to execute a player command.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .model import DisplayModel
from .notice import Notice, notice_text

ScreenKind = Literal["idle", "playback", "menu", "notice"]
Severity = Literal["info", "success", "warning", "error"]


@dataclass(frozen=True)
class ScreenItem:
    key: str
    label: str
    value: str | None = None
    focused: bool = False
    selected: bool = False
    enabled: bool = True
    intent: str | None = None

    def __post_init__(self):
        if not isinstance(self.key, str) or not self.key:
            raise ValueError("item key is required")
        if not isinstance(self.label, str):
            raise ValueError("item label must be text")
        if self.value is not None and not isinstance(self.value, str):
            raise ValueError("item value must be text")
        if any(type(v) is not bool for v in (self.focused, self.selected, self.enabled)):
            raise ValueError("item flags must be booleans")
        if self.intent is not None and (not isinstance(self.intent, str) or not self.intent):
            raise ValueError("intent must be a nonempty identifier")


@dataclass(frozen=True)
class SemanticScreen:
    """Everything a display needs to draw one screen, already translated.

    ``view`` names the screen ("navigation", "targets", "power", ...) so a
    graphical display can pick its widget; a text display may ignore it.
    ``subheading``, ``placeholder`` (shown when a list has no entries),
    ``status`` and ``footer`` are optional lines; displays without room
    for them may leave them out. On a menu, ``message``/``severity`` carry
    short feedback (e.g. a rejected speaker choice) that small displays
    show instead of the menu and large displays show in the status line.
    """
    kind: ScreenKind
    heading: str = ""
    items: tuple[ScreenItem, ...] = ()
    message: str | None = None
    severity: Severity | None = None
    view: str = ""
    subheading: str = ""
    placeholder: str = ""
    status: str = ""
    footer: str = ""

    def __post_init__(self):
        if self.kind not in ("idle", "playback", "menu", "notice"):
            raise ValueError("invalid screen kind")
        if not isinstance(self.heading, str) or not isinstance(self.items, tuple):
            raise ValueError("invalid screen heading or items")
        if not all(isinstance(v, str) for v in (
                self.view, self.subheading, self.placeholder, self.status, self.footer)):
            raise ValueError("screen text fields must be text")
        if any(not isinstance(item, ScreenItem) for item in self.items):
            raise ValueError("screen items must be ScreenItem instances")
        keys = [item.key for item in self.items]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate screen item keys")
        if sum(item.focused for item in self.items) > 1:
            raise ValueError("only one menu item may have focus")
        if self.kind == "notice":
            if not isinstance(self.message, str) or not self.message:
                raise ValueError("notice message is required")
            if self.severity not in ("info", "success", "warning", "error"):
                raise ValueError("notice severity is required")
            if self.items:
                raise ValueError("notice cannot contain menu items")
        elif self.kind == "menu" and self.message is not None:
            if not isinstance(self.message, str) or not self.message:
                raise ValueError("menu feedback must be text")
            if self.severity not in ("info", "success", "warning", "error"):
                raise ValueError("menu feedback needs a severity")
        elif self.message is not None or self.severity is not None:
            raise ValueError("only a notice or menu feedback may carry message or severity")


def screen_from_display_model(model: DisplayModel) -> SemanticScreen:
    """Minimal 01A bridge; full interactive menus get their adapter in 01B."""
    if not isinstance(model, DisplayModel):
        raise TypeError("expected DisplayModel")
    if model.view == "menu":
        items = tuple(ScreenItem(
            key=f"speaker:{index}", label=name,
            focused=index == model.focus_index,
            selected=(index in model.checked_indices if model.checkbox_mode
                      else index == model.selected_index),
            intent="select_speaker",
        ) for index, name in enumerate(model.speakers))
        return SemanticScreen(kind="menu", heading="speakers", items=items)
    if model.view not in ("idle", "playback"):
        raise ValueError("invalid display model view")
    rows = []
    if model.disc_present:
        if model.album:
            rows.append(ScreenItem("album", "album", value=model.album))
        if model.artist:
            rows.append(ScreenItem("artist", "artist", value=model.artist))
        if model.title:
            rows.append(ScreenItem("track_title", "track", value=model.title))
        if model.track_artist:
            rows.append(ScreenItem("track_artist", "artist", value=model.track_artist))
        if model.track_count:
            rows.append(ScreenItem("track", "track_number",
                                   value=f"{model.track}/{model.track_count}"))
    if model.speaker:
        rows.append(ScreenItem("speaker", "speaker", value=model.speaker))
    rows.append(ScreenItem("playback", "playback", value=model.playback))
    return SemanticScreen(kind=model.view, heading=model.view, items=tuple(rows))


def screen_from_notice(notice: Notice, *, language: str = "de") -> SemanticScreen:
    if not isinstance(notice, Notice):
        raise TypeError("expected Notice")
    return SemanticScreen(kind="notice", message=notice_text(notice, language),
                          severity=notice.severity, view="notice")
