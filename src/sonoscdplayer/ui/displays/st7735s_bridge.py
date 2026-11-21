"""Offline ST7735S bridge from existing semantic UI state to an RGB565 frame.

This module has no SPI, GPIO, Sonos actions, systemd or configuration writes.
It is not installed in display_service and does not enable display switching.
"""
from __future__ import annotations

from sonoscdplayer.hardware.displays.st7735s import DisplayConfig, image_to_frame
from sonoscdplayer.ui.menu.player_adapter import project_status
from sonoscdplayer.ui.renderers.landscape_160x128 import LandscapeRenderer
from sonoscdplayer.ui.screens.semantic_screen import SemanticScreen, screen_from_display_model


TESTED_CONFIG = DisplayConfig(rotation=270, x_offset=2, y_offset=1, color_order='rgb')


class ST7735SFrameBridge:
    """Pure rendering bridge; a live backend may use it after separate integration."""

    def __init__(self, *, config=TESTED_CONFIG, renderer=None):
        if not isinstance(config, DisplayConfig) or config.size != (160, 128):
            raise ValueError('ST7735S landscape bridge requires rotation 90 or 270')
        self.config = config
        self.renderer = renderer if renderer is not None else LandscapeRenderer()

    def from_screen(self, screen: SemanticScreen, *, elapsed=0):
        """Convert a translated semantic screen to a validated RGB565 frame."""
        if not isinstance(screen, SemanticScreen):
            raise TypeError('expected SemanticScreen')
        image = self.renderer.render(screen, elapsed=elapsed)
        return image_to_frame(image, self.config)

    def from_player_status(self, status: dict, *, elapsed=0):
        """Use the same strict player-status projection as the current UI."""
        model = project_status(status)
        return self.from_screen(screen_from_display_model(model), elapsed=elapsed)

    def from_menu(self, menu, *, now: float, elapsed=0):
        """Render the existing menu semantics, never dispatch its actions.

        A None semantic screen means an external owner (e.g. WLAN setup) is
        presenting its own screen. Do not substitute a misleading menu frame.
        """
        screen = menu.semantic_screen(now=now)
        if screen is None:
            return None
        return self.from_screen(screen, elapsed=elapsed)
