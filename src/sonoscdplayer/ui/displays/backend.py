"""Frame contracts and explicit backend registry.

This module contains no hardware implementations. Registration is explicit:
configuration values never import arbitrary Python modules.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol

from sonoscdplayer.ui.displays.capabilities import DisplayCapabilities


@dataclass(frozen=True)
class GraphicFrame:
    """A pixel frame for a graphic display, in the display's pixel format."""
    width: int
    height: int
    pixel_format: str
    data: bytes

    def __post_init__(self):
        if (type(self.width) is not int or self.width < 1
                or type(self.height) is not int or self.height < 1):
            raise ValueError("invalid graphic frame size")
        if not isinstance(self.pixel_format, str) or not self.pixel_format:
            raise ValueError("invalid pixel format")
        if not isinstance(self.data, bytes):
            raise ValueError("graphic frame requires immutable bytes")


@dataclass(frozen=True)
class CharacterFrame:
    """A text frame for a character display: one string per row."""
    lines: tuple[str, ...]

    def __post_init__(self):
        if not isinstance(self.lines, tuple) or any(
                not isinstance(line, str) for line in self.lines):
            raise ValueError("character frame requires text lines")


class DisplayBackend(Protocol):
    """What a display driver offers the display service."""
    capabilities: DisplayCapabilities

    def open(self) -> None:
        """Initialize the display."""
        ...

    def show(self, frame: GraphicFrame | CharacterFrame) -> None:
        """Show one frame."""
        ...

    def close(self) -> None:
        """Release the display."""
        ...


def validate_frame(capabilities: DisplayCapabilities,
                   frame: GraphicFrame | CharacterFrame) -> None:
    """Validate geometry and format, before handing output to a device."""
    if capabilities.kind == "graphic":
        if not isinstance(frame, GraphicFrame):
            raise ValueError("graphic display needs GraphicFrame")
        if (frame.width, frame.height, frame.pixel_format) != (
                capabilities.width, capabilities.height, capabilities.pixel_format):
            raise ValueError("graphic frame does not match display")
        if frame.pixel_format == "gray4-packed":
            if frame.width % 2 or len(frame.data) != frame.width * frame.height // 2:
                raise ValueError("invalid packed gray4 frame length")
        elif frame.pixel_format == "rgb565":
            if len(frame.data) != frame.width * frame.height * 2:
                raise ValueError("invalid rgb565 frame length")
        elif frame.pixel_format == "mono1-vlsb":
            # One byte per column and eight vertical pixels, bit 0 at top.
            if frame.height % 8 or len(frame.data) != frame.width * frame.height // 8:
                raise ValueError("invalid mono1-vlsb frame length")
        # Other formats need their own explicitly validated packing in 01B/01C.
        else:
            raise ValueError("frame packing not yet supported")
    else:
        if not isinstance(frame, CharacterFrame):
            raise ValueError("character display needs CharacterFrame")
        if len(frame.lines) != capabilities.rows or any(
                len(line) != capabilities.columns for line in frame.lines):
            raise ValueError("character frame dimensions do not match display")
        # Glyph mapping is the responsibility of the 01B charset adapter.


class BackendRegistry:
    """Display backends by name, created on demand."""
    def __init__(self):
        self._factories: dict[str, Callable[[], DisplayBackend]] = {}

    def register(self, name: str, factory: Callable[[], DisplayBackend]) -> None:
        """Register a factory for the backend ``name``."""
        if not isinstance(name, str) or not name or not name.replace("_", "").isalnum():
            raise ValueError("invalid backend identifier")
        if name in self._factories:
            raise ValueError("backend already registered")
        if not callable(factory):
            raise TypeError("backend factory must be callable")
        self._factories[name] = factory

    def create(self, name: str) -> DisplayBackend:
        """Create the backend ``name``; raises KeyError if unknown."""
        try:
            factory = self._factories[name]
        except (KeyError, TypeError) as exc:
            raise ValueError("unknown display backend") from exc
        backend = factory()
        if not isinstance(getattr(backend, "capabilities", None), DisplayCapabilities):
            raise TypeError("backend must declare valid display capabilities")
        return backend
