"""Opt-in, frame-only seam for the two display rendering paths.

NOT installed in display_service. No controller selection, SPI/GPIO, service
restart, input handling or configuration writes occur here. In particular,
constructing this router does not instantiate a hardware display backend.
"""
from __future__ import annotations



def drive_notice_screen(state, *, language='de'):
    """Translate the same latched drive signals as the SSD1327 display.

    This is a read-only semantic projection; it never wakes or retries a drive.
    """
    from sonoscdplayer.ui.notices.notice import Notice
    from sonoscdplayer.ui.screens.semantic_screen import screen_from_notice

    power = state.get('drive_power') if isinstance(state, dict) else None
    if not isinstance(power, dict):
        return None
    if power.get('idle_wake_fault_latched') is True:
        retry = power.get('idle_wake_retry_in_seconds')
        if type(retry) is not int or retry < 0:
            retry = None
        if power.get('idle_wake_retries_exhausted') is True:
            key, params = 'status.drive_wake_exhausted', {}
        elif retry is None:
            key, params = 'status.drive_wake_fault', {}
        elif retry > 0:
            key, params = 'status.drive_wake_retry', {'seconds': retry}
        else:
            key, params = 'status.drive_wake_retry_ready', {}
        return screen_from_notice(Notice(key, severity='error', params=params),
                                  language=language)
    if power.get('idle_waking') is True:
        return screen_from_notice(Notice('status.drive_waking'), language=language)
    return None


def special_menu_owner(menu):
    """These screen owners outrank even a latched drive warning.

    Mirror selected_status_frame's high-priority checks, without querying
    the controller for state or executing any menu action.
    """
    if (getattr(menu, 'power_menu', False) is True or
            isinstance(getattr(menu, 'shutdown_screen', None), str)):
        return True
    snapshot = getattr(menu, 'netsetup_snapshot', None)
    if callable(snapshot):
        result = snapshot()
        # A non-None external owner whose state is unknown must not be
        # overwritten with ordinary player status either.
        return result is not None
    return False


class DisplayFrameRouter:
    """Route read-only state to the legacy OLED frame or the ST7735S bridge.

    The SSD1327 path delegates to the unchanged production frame selector.
    The ST7735S path is preview-only; it deliberately does not claim to
    implement every SSD1327-specific startup or hardware shutdown sequence yet.
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

        # Match the live SSD1327 priority: shutdown/POWER/WLAN ownership,
        # then verified drive wake/fault notices, then ordinary menu/volume.
        # None from the menu means an externally owned screen, not permission
        # for the frame router to invent WLAN setup content.
        if menu is not None and special_menu_owner(menu):
            screen = menu.semantic_screen(now=now)
            return (None if screen is None else
                    self._st_bridge.from_screen(screen, elapsed=elapsed))

        drive_screen = drive_notice_screen(state, language=language)
        if drive_screen is not None:
            return self._st_bridge.from_screen(drive_screen, elapsed=elapsed)

        if menu is not None:
            screen = menu.semantic_screen(now=now)
            if screen is not None:
                return self._st_bridge.from_screen(screen, elapsed=elapsed)
            # A still-active menu or overlay must not be replaced by status.
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
