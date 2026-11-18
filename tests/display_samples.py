"""Sample screens for display renderer tests (formerly the diagnostics' demo data).

Neutral placeholder data; nothing comes from a player, speaker or network.
"""
from sonos_ui.compact_graphic import compact_screen_from_model
from sonos_ui.model import DisplayModel
from sonos_ui.semantic_screen import ScreenItem, SemanticScreen

MENU_LABELS = ('Room one', 'Room two', 'A very long room name for scrolling',
               'Room four', 'Room five', 'Back')


def overview_screen(elapsed=0):
    return compact_screen_from_model(DisplayModel(
        view='playback', track=3, track_count=12,
        title='A long sample title that has to scroll on the display',
        artist='Sample artist', album='Sample album', speaker='Room one',
        playback='playing', elapsed_seconds=83 + int(elapsed), duration_seconds=252))


def menu_screen(focus):
    return SemanticScreen(kind='menu', heading='Speakers', view='targets', items=tuple(
        ScreenItem(f'sample:{index}', label, focused=index == focus,
                   selected=index == 0, intent='back' if index == 5 else 'select_speaker')
        for index, label in enumerate(MENU_LABELS)))


def all_screens():
    yield 'overview', overview_screen()
    for focus in range(len(MENU_LABELS)):
        yield f'menu-{focus + 1}', menu_screen(focus)
