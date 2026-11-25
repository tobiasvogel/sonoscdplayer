"""Compare 9px status/footer text using the actual 128x128 UI renderers.

Synthetic data only. Offline by default; explicit hardware execution reuses
the configured OLED backend and never interacts with player or menu inputs.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
from functools import partial
import json
import math
from pathlib import Path
import signal
import sys
import time

from sonoscdplayer.hardware.displays.frames import image_to_frame
from sonoscdplayer.ui.menu.navigation_pages import NavigationRenderer
from sonoscdplayer.ui.renderers.oled_128 import Renderer
from sonoscdplayer.ui.renderers.oled_128_speakers import MultiRenderer
from sonoscdplayer.ui.renderers.oled_128_screens import render_screen
from sonoscdplayer.ui.screens.model import DisplayModel
from sonoscdplayer.ui.screens.semantic_screen import ScreenItem, SemanticScreen

LONG_ROOM = 'Wohnzimmer und Gästezimmer im Obergeschoss'


def scenes():
    """Real overview/menu models, including long text and explicit scroll snapshots."""
    base = DisplayModel(album='Beispielalbum', artist='Beispielinterpret',
                        title='Ein neuer Tag', track=3, track_count=12,
                        speaker='Wohnzimmer', elapsed_seconds=83, duration_seconds=245)
    playing = replace(base, view='playback', playback='playing')
    return (
        ('overview', replace(base, disc_phase='STOPPED'), 0),
        ('playback', playing, 0),
        ('long-room-start', replace(playing, speaker=LONG_ROOM), 0),
        ('long-room-scroll', replace(playing, speaker=LONG_ROOM), 3),
        ('speakers', SemanticScreen('menu', view='targets', heading='Lautsprecher',
            subheading='Ziele auswählen', status='2 markiert', footer='Drehen / Drücken',
            items=(ScreenItem('living', 'Wohnzimmer', selected=True),
                   ScreenItem('kitchen', 'Küche', focused=True, selected=True),
                   ScreenItem('guest', 'Gästezimmer'))), 0),
        ('settings', SemanticScreen('menu', view='navigation', heading='Einstellungen',
            status='Bereit', footer='Drücken: Auswahl bestätigen',
            items=(ScreenItem('language', 'Sprache', focused=True),
                   ScreenItem('network', 'Netzwerk'), ScreenItem('menu.back', 'Zurück'))), 0),
        ('power', SemanticScreen('menu', view='power', heading='Ausschalten',
            status='ÄÖÜ äöü ßgjpqy', footer='Drehen / Drücken',
            items=(ScreenItem('back', 'Zurück', focused=True),
                   ScreenItem('off', 'Ausschalten'), ScreenItem('reboot', 'Neustart'))), 0),
    )


def render_scene(model, *, raster='pixel', elapsed=0):
    """Render an unchanged UI view, varying the configured text rasterization."""
    if raster not in ('pixel', 'aa'):
        raise ValueError('unknown raster mode')
    aa = raster == 'aa'
    renderer = Renderer(use_antialiasing=aa)
    renderer.bottom = partial(renderer.bottom, elapsed=elapsed)
    if isinstance(model, DisplayModel):
        # Pin title/artist scrolling so snapshots isolate status/footer movement.
        return renderer.render(model, elapsed=0, grayscale=False)
    multi = MultiRenderer(use_antialiasing=aa)
    nav = NavigationRenderer(use_antialiasing=aa)
    for target in (multi, nav):
        target.bottom = partial(target.bottom, elapsed=elapsed)
    return render_screen(model, renderer=multi, navigation_renderer=nav, gray=False)


def _stop(_signum, _frame):
    raise SystemExit(0)


def main(argv=None):
    """Export previews or show finite real-layout samples on the configured OLED."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--gpio', action='store_true')
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--display-config', type=Path)
    parser.add_argument('--preview-dir', type=Path)
    parser.add_argument('--raster', choices=('pixel', 'aa', 'both'), default='pixel')
    parser.add_argument('--manual', action='store_true')
    parser.add_argument('--seconds', type=float, default=6)
    args = parser.parse_args(argv)
    if args.gpio != args.execute:
        parser.error('--gpio and --execute must be given together')
    if not math.isfinite(args.seconds) or not .5 <= args.seconds <= 60:
        parser.error('--seconds must be finite and 0.5..60')
    if args.manual and (not args.execute or not sys.stdin.isatty()):
        parser.error('--manual requires hardware execution and an interactive terminal')
    backend = config = None
    if args.execute:
        from sonoscdplayer.hardware.displays.pins import load_display_pins
        from sonoscdplayer.hardware.displays.runtime import backend_for
        from sonoscdplayer.system.config_paths import player_config
        try:
            config = load_display_pins(args.display_config or player_config())
            backend = backend_for(config.controller)
        except (OSError, ValueError) as exc:
            parser.error('invalid display configuration: '+str(exc))
    modes = ('pixel', 'aa') if args.raster == 'both' else (args.raster,)
    prepared = []
    for name, model, elapsed in scenes():
        for raster in modes:
            image = render_scene(model, raster=raster, elapsed=elapsed)
            prepared.append((name, raster, image_to_frame(image, color=backend.color if backend else True)))
            if args.preview_dir:
                args.preview_dir.mkdir(parents=True, exist_ok=True)
                image.save(args.preview_dir/f'{name}-{raster}.png')
                image.convert('L').save(args.preview_dir/f'{name}-{raster}-gray.png')
    driver = None
    previous_sigterm = None
    try:
        if backend:
            from sonoscdplayer.system.gpio_settings import gpio_warnings_enabled
            driver = backend.create(config, gpio_warnings=gpio_warnings_enabled(
                args.display_config or player_config()))
            previous_sigterm = signal.signal(signal.SIGTERM, _stop)
            driver.open(adopt_existing=False)
        for name, raster, frame in prepared:
            print(json.dumps(dict(scene=name, raster=raster, hardware=args.execute,
                                  frame_bytes=len(frame.data))), flush=True)
            if driver:
                driver.show(frame)
                if args.manual:
                    try:
                        input('Enter: weiter/beenden | Ctrl-C: abbrechen ')
                    except EOFError:
                        break
                else:
                    time.sleep(args.seconds)
    finally:
        pending_error = sys.exc_info()[0] is not None
        try:
            if driver:
                try:
                    driver.close(blank=True)
                except Exception as exc:
                    if not pending_error:
                        raise
                    print(f'Display cleanup also failed: {exc}', file=sys.stderr)
        finally:
            if previous_sigterm is not None:
                signal.signal(signal.SIGTERM, previous_sigterm)


if __name__ == '__main__':
    main()
