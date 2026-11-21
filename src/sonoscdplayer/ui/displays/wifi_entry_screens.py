"""Read-only semantic ST7735S preview of the existing Wi-Fi entry controller.

Never calls WifiEntryController.event/sync, Session methods or network APIs.
Only the character under the encoder cursor is shown in password mode;
previously entered password characters are masked.
"""
from __future__ import annotations

from sonoscdplayer.network_setup.session import SetupSnapshot
from sonoscdplayer.ui.menu.wifi_entry import RESCAN, HIDDEN, BACK
from sonoscdplayer.ui.menu.text_input import DONE, DELETE, CANCEL
from sonoscdplayer.ui.screens.semantic_screen import ScreenItem, SemanticScreen


def wifi_entry_screen(controller, snapshot, *, language='de'):
    """Project a currently active manual Wi-Fi screen, or return None.

    A missing/inconsistent controller must not be presented as a working
    chooser. The caller retains external screen ownership in that case.
    """
    if not isinstance(snapshot, SetupSnapshot):
        return None
    if language not in ('de', 'en'):
        raise ValueError('unsupported setup preview language')
    de = language == 'de'
    if snapshot.state == 'scanning' and snapshot.manual:
        return SemanticScreen('notice', view='wifi_manual',
                              message='WLAN wird gesucht' if de else 'Scanning Wi-Fi networks',
                              severity='info')
    if snapshot.state != 'choosing' or controller is None:
        return None
    mode = getattr(controller, 'mode', None)
    if mode == 'list':
        entries = controller.items(snapshot)
        focus = getattr(controller, 'focus', None)
        if type(focus) is not int or not 0 <= focus < len(entries):
            return None
        rows = []
        for index, item in enumerate(entries):
            if item is RESCAN:
                label = 'Erneut suchen' if de else 'Rescan'
            elif item is HIDDEN:
                label = 'Verstecktes WLAN' if de else 'Hidden network'
            elif item is BACK:
                label = 'Zurück' if de else 'Back'
            else:
                label = getattr(item, 'ssid', None)
                if not isinstance(label, str) or not label:
                    return None
                if getattr(item, 'secure', False):
                    label += '  [*]'
                elif item.ssid in snapshot.saved:
                    label += '  [saved]'
            rows.append(ScreenItem(f'wifi:{index}', label, focused=index == focus,
                                   intent='wifi_preview_only'))
        return SemanticScreen('menu', heading='WLAN auswählen' if de else 'Choose Wi-Fi',
                              view='wifi_manual', items=tuple(rows),
                              status='Drehencoder verwenden' if de else 'Use rotary encoder')
    if mode not in ('ssid', 'password'):
        return None
    entry = getattr(controller, 'entry', None)
    if entry is None or not isinstance(getattr(entry, 'text', None), str):
        return None
    cursor = getattr(entry, 'current', None)
    action_labels = {
        DONE: 'Fertig' if de else 'Done',
        DELETE: 'Löschen' if de else 'Delete',
        CANCEL: 'Abbrechen' if de else 'Cancel',
    }
    if not isinstance(cursor, str):
        return None
    selection = action_labels.get(cursor, cursor)
    if cursor not in action_labels and (len(cursor) != 1 or not cursor.isprintable()):
        return None
    secret = mode == 'password'
    typed = '*' * len(entry.text) if secret else entry.text
    title = ('Passwort eingeben' if de else 'Enter password') if secret else (
        'WLAN-Name eingeben' if de else 'Enter Wi-Fi name')
    rows = (
        ScreenItem('wifi:value', ('Eingabe' if de else 'Input') + ': ' + (typed or '—'),
                   value=typed),
        ScreenItem('wifi:cursor', selection, focused=True, intent='wifi_preview_only'),
    )
    # Do not copy entry.error parameters or password data to status/messages.
    status = ('Eingabe prüfen' if de else 'Check input') if entry.error else (
        'Drücken zum Auswählen' if de else 'Press to select')
    return SemanticScreen('menu', heading=title, view='wifi_manual', items=rows,
                          subheading=getattr(controller, 'target', '') if secret else '',
                          status=status)
