"""Semantic RGB palette; SSD1327 conversion occurs only in the output adapter."""
from dataclasses import dataclass

@dataclass(frozen=True)
class Theme:
    background: str = '#0B0D10'
    surface: str = '#171B20'
    primary: str = '#F4F3F0'
    secondary: str = '#A5ADB4'
    muted: str = '#54606B'
    accent: str = '#C6A46A'
    playing: str = '#66C59D'
    warning: str = '#E7AE67'
    selection_text: str = '#101318'

MODERN_HIFI = Theme()


def ssd1327_gray(rgb_image):
    """Quantize luminance to 16 explicit grayscale values; RGB layout unchanged."""
    return rgb_image.convert('L').point(lambda value: round(value / 17) * 17)
