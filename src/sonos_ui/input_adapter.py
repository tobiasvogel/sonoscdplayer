"""Pure GPIO event to UI action adapter; never sends player commands."""

def to_ui_action(name, event):
    if name == 'encoder':
        return {'right': 'navigate_next', 'left': 'navigate_previous'}.get(event)
    if event != 'pressed':
        return None
    if name == 'encoder_sw':
        return 'confirm'
    if name == 'connect':
        return 'open_speakers'
    return None
