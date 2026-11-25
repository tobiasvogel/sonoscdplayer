"""Read-only, ST7735S-preview semantic projections of special screen owners.

No setup control, input handling, hardware access or configuration writes.
The manual Wi-Fi chooser/text-entry screen is deliberately NOT projected:
it needs its own interactive presenter before live ST7735S support.
"""
from __future__ import annotations

from sonoscdplayer.network_setup.session import SetupSnapshot
from sonoscdplayer.ui.i18n import Translator
from sonoscdplayer.ui.screens.semantic_screen import ScreenItem, SemanticScreen
from sonoscdplayer.ui.screens.semantic_views import system_screen


def startup_or_shutdown_screen(name: str, *, language='de') -> SemanticScreen:
    """Reuse the established localized system messages; no shutdown actions."""
    return system_screen(name, language=language)


def wifi_setup_screen(snapshot, *, language='de') -> SemanticScreen | None:
    """Present confirmed setup progress; None delegates interactive entry elsewhere.

    Only the project's typed SetupSnapshot is accepted. In particular, an
    unknown snapshot or unknown phase must not be misreported as successful.
    """
    if not isinstance(snapshot, SetupSnapshot):
        return None
    tr = Translator(language).tr
    state = snapshot.state
    if state in ('idle', 'scanning', 'choosing') or snapshot.manual:
        return None  # external interactive entry owns these screens
    if state == 'hotspot':
        # SSID and password are credentials already shown by the existing
        # device setup view. Do not log, persist or transmit them.
        rows = (
            ('ssid', 'SSID', snapshot.hotspot_ssid),
            ('password', tr('netsetup.password_title'), snapshot.password),
            ('url', tr('netsetup.address'), snapshot.url),
        )
        items = tuple(ScreenItem(key, f'{label}: {value}', value=value, focused=i == 0)
                      for i, (key, label, value) in enumerate(
                          (row for row in rows if row[2])))
        return SemanticScreen('menu', heading=tr('netsetup.title'), view='wifi_setup',
                              items=items, status=tr('netsetup.display_only'))
    # The same texts as the setup screens of the OLED.
    if state == 'preparing':
        message = tr('netsetup.preparing')
    elif state == 'connecting':
        message = (tr('netsetup.connecting', ssid=snapshot.target) if snapshot.target
                   else tr('netsetup.connecting_unnamed'))
    elif state == 'connected':
        message = (tr('netsetup.connected', ssid=snapshot.target) if snapshot.target
                   else tr('netsetup.connected_unnamed'))
    elif state == 'unavailable':
        message = tr({'no_wifi': 'netsetup.no_wifi',
                      'connect_failed': 'netsetup.failed'}.get(snapshot.error,
                                                               'netsetup.hotspot_failed'))
    else:
        return None
    return SemanticScreen('notice', view='wifi_setup', message=message,
                          severity='error' if state == 'unavailable' else 'info')
