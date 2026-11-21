"""Read-only, ST7735S-preview semantic projections of special screen owners.

No setup control, input handling, hardware access or configuration writes.
The manual Wi-Fi chooser/text-entry screen is deliberately NOT projected:
it needs its own interactive presenter before live ST7735S support.
"""
from __future__ import annotations

from sonoscdplayer.network_setup.session import SetupSnapshot
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
    if language not in ('de', 'en'):
        raise ValueError('unsupported setup preview language')
    de = language == 'de'
    state = snapshot.state
    if state in ('idle', 'scanning', 'choosing') or snapshot.manual:
        return None  # external interactive entry owns these screens
    titles = {
        'preparing': ('WLAN wird vorbereitet', 'Preparing Wi-Fi'),
        'hotspot': ('WLAN einrichten', 'Set up Wi-Fi'),
        'connecting': ('WLAN verbindet', 'Connecting Wi-Fi'),
        'connected': ('WLAN verbunden', 'Wi-Fi connected'),
        'unavailable': ('WLAN nicht verfügbar', 'Wi-Fi unavailable'),
    }
    if state not in titles:
        return None
    title = titles[state][0 if de else 1]
    if state == 'hotspot':
        # SSID and password are credentials already shown by the existing
        # device setup view. Do not log, persist or transmit them.
        rows = (
            ('ssid', 'SSID', snapshot.hotspot_ssid),
            ('password', 'Passwort' if de else 'Password', snapshot.password),
            ('url', 'Adresse' if de else 'Address', snapshot.url),
        )
        items = tuple(ScreenItem(key, f'{label}: {value}', value=value, focused=i == 0)
                      for i, (key, label, value) in enumerate(
                          (row for row in rows if row[2])))
        return SemanticScreen('menu', heading=title, view='wifi_setup', items=items,
                              status=('Nur Anzeige' if de else 'Display only'))
    if state == 'connecting':
        detail = snapshot.target or ('Verbindung läuft' if de else 'Connecting')
    elif state == 'connected':
        detail = snapshot.target or ('Verbunden' if de else 'Connected')
    elif state == 'unavailable':
        detail = 'WLAN nicht verfügbar' if de else 'Wi-Fi unavailable'
    else:
        detail = 'Bitte warten' if de else 'Please wait'
    return SemanticScreen('notice', view='wifi_setup', message=f'{title}: {detail}',
                          severity='error' if state == 'unavailable' else 'info')
