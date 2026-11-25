"""Synthetic SSD1351 UI samples using the production 128x128 RGB widgets.

No player, Sonos, inputs or service configuration is accessed. Focus changes
and checkbox marks are fixtures, never commands sent to a real speaker.
"""
from dataclasses import replace

from sonoscdplayer.ui.renderers.oled_128 import Renderer
from sonoscdplayer.ui.renderers.oled_128_speakers import MultiRenderer
from sonoscdplayer.ui.renderers.oled_128_screens import render_screen
from sonoscdplayer.ui.screens.model import DisplayModel
from sonoscdplayer.ui.screens.semantic_screen import ScreenItem, SemanticScreen


def overview_models():
    """Stopped, playing and paused examples with the same local track data."""
    base = DisplayModel(album='Beispielalbum', artist='Beispielinterpret',
                        title='Ein neuer Tag', track=3, track_count=12,
                        speaker='Wohnzimmer', elapsed_seconds=83,
                        duration_seconds=245)
    return (
        ('overview-stopped', replace(base, disc_phase='STOPPED')),
        ('overview-playing', replace(base, view='playback', playback='playing')),
        ('overview-paused', replace(base, view='playback', playback='paused')),
    )


def menu_screens():
    """Move focus through rooms/apply/back, then preview a second check mark."""
    labels = ('Wohnzimmer', 'Küche', 'Gästezimmer', 'Arbeitszimmer')
    for step, (focus, checked) in enumerate(
            [(focus, (0,)) for focus in range(6)] + [(1, (0, 1))]):
        items = tuple(ScreenItem(f'room-{index}', label,
                                 focused=index == focus, selected=index in checked,
                                 intent='select_speaker')
                      for index, label in enumerate(labels))
        items += (ScreenItem('apply', 'Übernehmen', focused=focus == 4,
                             intent='review_target'),
                  ScreenItem('back', 'Zurück', focused=focus == 5, intent='back'))
        yield (f'menu-{step}', SemanticScreen(
            kind='menu', view='targets', heading='Lautsprecher',
            subheading='Ziele auswählen', items=items,
            status=f'{len(checked)} markiert', footer='Drehen / Drücken'))


def demo_images(demo):
    """Render finite demo scenes in RGB, with the normal layout and palette."""
    if demo not in ('overview', 'menu', 'all'):
        raise ValueError('unknown SSD1351 demo')
    images = []
    if demo in ('overview', 'all'):
        renderer = Renderer()
        images.extend((name, renderer.render(model, elapsed=0, grayscale=False))
                      for name, model in overview_models())
    if demo in ('menu', 'all'):
        renderer = MultiRenderer()
        images.extend((name, render_screen(screen, renderer=renderer, gray=False))
                      for name, screen in menu_screens())
    return images
