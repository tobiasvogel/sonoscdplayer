"""Opt-in, frame-only seam for the two display rendering paths.

NOT installed in display_service. No controller selection, SPI/GPIO, service
restart, input handling or configuration writes occur here. In particular,
constructing this router does not instantiate a hardware display backend.
"""
from __future__ import annotations



class DisplayFrameRouter:
    """Route read-only state to the legacy OLED frame or the ST7735S bridge.

    The SSD1327 path delegates to the unchanged production frame selector.
    The ST7735S path is preview-only; it deliberately does not claim to
    implement every SSD1327-specific start, drive or shutdown screen yet.
    """

    TARGETS = ('ssd1327', 'st7735s')

    def __init__(self, target='ssd1327', *, oled_frame=None, st_bridge=None):
        if target not in self.TARGETS:
            raise ValueError('display frame target is not supported: ' + str(target))
        self.target = target
        self._oled_frame = oled_frame
        self._st_bridge = st_bridge

    def frame(self, state, *, menu=None, now=None, elapsed=0,
              language='de', cover_mode='auto'):
        """Produce one display frame without opening any display hardware.

        ``now`` is required with a menu: the menu's semantic selection may be
        time-dependent (volume overlay, notifications). If WLAN setup owns the
        screen, return None rather than silently showing a player frame.
        """
        if menu is not None and now is None:
            raise ValueError('now is required when rendering a menu')
        if self.target == 'ssd1327':
            if self._oled_frame is None:
                from sonoscdplayer.ui.display_service import selected_status_frame
                self._oled_frame = selected_status_frame
            # Do not reimplement or alter OLED's existing priority, notices,
            # startup, network setup and error handling.
            return self._oled_frame(state, menu=menu, cover_mode=cover_mode,
                                    language=language)

        if self._st_bridge is None:
            from sonoscdplayer.ui.displays.st7735s_bridge import ST7735SFrameBridge
            self._st_bridge = ST7735SFrameBridge()

        if menu is not None:
            screen = menu.semantic_screen(now=now)
            if screen is not None:
                return self._st_bridge.from_screen(screen, elapsed=elapsed)
            # An external owner (WLAN setup) or an active but unprojectable
            # overlay must not be replaced by a misleading status screen.
            netsetup = getattr(menu, 'netsetup_snapshot', None)
            if callable(netsetup) and netsetup() is not None and not getattr(menu, 'power_menu', False):
                return None
            if any(bool(getattr(menu, name, False)) for name in
                   ('active', 'power_menu', 'language_picker', 'shutdown_screen')):
                return None

        if state is None:
            # The preview can represent an offline player as a semantic notice;
            # no display-specific fallback or player RPC is performed.
            from sonoscdplayer.ui.notices.notice import Notice
            from sonoscdplayer.ui.screens.semantic_screen import screen_from_notice
            screen = screen_from_notice(Notice('status.player_offline'), language=language)
            return self._st_bridge.from_screen(screen, elapsed=elapsed)
        return self._st_bridge.from_player_status(state, elapsed=elapsed)
