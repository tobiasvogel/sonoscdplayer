"""Validated, hardware-independent display capabilities.

Capabilities describe output, not a physical connection or controller driver.
A character display is not treated as a tiny pixel framebuffer.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

DisplayKind = Literal["graphic", "character"]
RefreshPolicy = Literal["interactive", "event_driven"]


@dataclass(frozen=True)
class DisplayCapabilities:
    kind: DisplayKind
    width: int | None = None
    height: int | None = None
    columns: int | None = None
    rows: int | None = None
    pixel_format: str | None = None
    charset: str | None = None
    refresh_policy: RefreshPolicy = "interactive"
    min_refresh_ms: int = 100

    def __post_init__(self):
        if self.kind not in ("graphic", "character"):
            raise ValueError("unsupported display kind")
        if self.refresh_policy not in ("interactive", "event_driven"):
            raise ValueError("unsupported refresh policy")
        if type(self.min_refresh_ms) is not int or self.min_refresh_ms < 1:
            raise ValueError("min_refresh_ms must be a positive integer")
        if self.kind == "graphic":
            if (type(self.width) is not int or self.width < 1
                    or type(self.height) is not int or self.height < 1):
                raise ValueError("graphic display requires positive pixel dimensions")
            if not isinstance(self.pixel_format, str) or not self.pixel_format:
                raise ValueError("graphic display requires a pixel format")
            if any(value is not None for value in
                   (self.columns, self.rows, self.charset)):
                raise ValueError("graphic display cannot declare character geometry")
        else:
            if (type(self.columns) is not int or self.columns < 1
                    or type(self.rows) is not int or self.rows < 1):
                raise ValueError("character display requires positive columns and rows")
            if not isinstance(self.charset, str) or not self.charset:
                raise ValueError("character display requires a charset")
            if any(value is not None for value in
                   (self.width, self.height, self.pixel_format)):
                raise ValueError("character display cannot declare pixel geometry")


SSD1327_CAPABILITIES = DisplayCapabilities(
    kind="graphic", width=128, height=128, pixel_format="gray4-packed")
