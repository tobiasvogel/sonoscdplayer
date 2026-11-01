"""Hardware-independent display profiles for upcoming physical evaluation.

No driver or pin mapping is implied. All proposed defaults are provisional until
the exact module variant and hardware tests confirm orientation/interface.
"""
from dataclasses import dataclass
from .layout_config import DisplayProfile, show_cover


@dataclass(frozen=True)
class HardwareDisplayProfile:
    key: str
    controller: str
    width: int
    height: int
    color_mode: str
    cover_auto: bool
    refresh_policy: str
    driver_ready: bool = False

    def __post_init__(self):
        if not self.key or not self.controller or self.width <= 0 or self.height <= 0:
            raise ValueError('invalid display profile')
        if self.color_mode not in ('gray4', 'rgb565', 'mono1', 'four_color'):
            raise ValueError('invalid color mode')
        if self.refresh_policy not in ('interactive', 'event_driven'):
            raise ValueError('invalid refresh policy')

    def cover_enabled(self, mode='auto'):
        return show_cover(mode, DisplayProfile(
            width=self.width, height=self.height,
            supports_cover_auto=self.cover_auto
        ))


PROFILES = {
    'ssd1327_128x128': HardwareDisplayProfile(
        'ssd1327_128x128', 'SSD1327', 128, 128, 'gray4', False,
        'interactive', driver_ready=True),
    'st7735s_128x160': HardwareDisplayProfile(
        'st7735s_128x160', 'ST7735S', 128, 160, 'rgb565', True,
        'interactive'),
    'sh1107_64x128': HardwareDisplayProfile(
        'sh1107_64x128', 'SH1107', 64, 128, 'mono1', False,
        'interactive'),
    'epaper_200x200_4color': HardwareDisplayProfile(
        'epaper_200x200_4color', 'UNKNOWN', 200, 200, 'four_color',
        True, 'event_driven'),
}


def profile_for(key):
    return PROFILES[key]
