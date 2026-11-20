"""What the display says after PLAY/PAUSE was pressed.

The button service reports the press (and when it did not even ask the
player); the player reports a refused PLAY in its status as
``last_rejection``. This module only turns those into notice keys.
"""

#: How long "starting playback" stays at most; the playback screen or a
#: rejection replaces it earlier.
REQUEST_SECONDS = 30.0
#: How long a rejection stays visible.
REJECTION_SECONDS = 5.0


def rejection_notice(reason):
    """Notice key for the reason the player gave for refusing PLAY."""
    text = str(reason or '').lower()
    if 'not alone in its group' in text or 'member not standalone' in text:
        return 'play.rejected_grouped'
    if 'not stopped' in text or 'active transport' in text:
        return 'play.rejected_busy'
    if 'not found' in text or 'not discovered' in text or 'member discovery' in text:
        return 'play.rejected_not_found'
    if 'no confirmed target' in text:
        return 'play.no_target'
    if 'disc' in text:
        return 'play.rejected_disc'
    if 'drive' in text:
        return 'play.rejected_drive'
    return 'play.blocked'
