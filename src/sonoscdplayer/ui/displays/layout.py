"""Small display layout policy; cover availability and display capability are separate.

AUTO intentionally disables the cover on the compact 128x128 SSD1327 profile.
A future larger display adapter may opt in without changing the renderer.
"""
from dataclasses import dataclass

COVER_MODES = ('auto', 'on', 'off')


@dataclass(frozen=True)
class DisplayProfile:
    width: int = 128
    height: int = 128
    supports_cover_auto: bool = False

    def __post_init__(self):
        if self.width < 1 or self.height < 1:
            raise ValueError('display dimensions must be positive')


def show_cover(mode='auto', profile=DisplayProfile()):
    if mode not in COVER_MODES:
        raise ValueError('cover mode must be auto, on or off')
    return mode == 'on' or (mode == 'auto' and profile.supports_cover_auto)
