#!/usr/bin/env python3
"""M0.12c-r1: passive GPIO diagnostic and reproducible simulation; never controls Sonos."""
import argparse
import json
import time
from pathlib import Path

try:
    from .input_core import load_config, InputEngine
except ImportError:
    from input_core import load_config, InputEngine

DEFAULT_CONFIG = Path(__file__).with_name('interface.toml')


def pins_for(cfg):
    return {b.pin: b.pull for b in cfg.buttons.values()} | {
        cfg.encoder.a: cfg.encoder.pull, cfg.encoder.b: cfg.encoder.pull,
        cfg.encoder.switch.pin: cfg.encoder.switch.pull}


def run_sim(cfg, input_file):
    """JSON Lines: {\"ms\":0, \"levels\":{\"17\":0,...}}; first is full snapshot."""
    engine = None
    levels = {}
    with open(input_file, encoding='utf8') as f:
        for line in f:
            if not line.strip() or line.lstrip().startswith('#'):
                continue
            step = json.loads(line)
            levels.update({int(k): int(v) for k, v in step['levels'].items()})
            if not all(pin in levels for pin in pins_for(cfg)):
                raise ValueError('First simulation sample must include all configured GPIO pins')
            if any(v not in (0, 1) for v in levels.values()):
                raise ValueError('Simulation levels must be 0 or 1')
            if engine is None:
                engine = InputEngine(cfg, levels, step['ms'])
            else:
                for name, event in engine.sample(levels, step['ms']):
                    print(f'{step["ms"]}ms {name}: {event}', flush=True)


def run_gpio(cfg):
    try:
        import RPi.GPIO as GPIO
    except ImportError as exc:
        raise RuntimeError('RPi.GPIO not installed; use --simulate FILE on other systems') from exc
    pulls = {'off': GPIO.PUD_OFF, 'up': GPIO.PUD_UP, 'down': GPIO.PUD_DOWN}
    pins = pins_for(cfg)
    try:
        GPIO.setmode(GPIO.BCM)
        for pin, pull in pins.items():
            GPIO.setup(pin, GPIO.IN, pull_up_down=pulls[pull])
        sample = lambda: {pin: GPIO.input(pin) for pin in pins}
        engine = InputEngine(cfg, sample(), time.monotonic_ns() // 1_000_000)
        print('GPIO diagnostics active; Ctrl+C to exit. No Sonos commands.', flush=True)
        while True:
            for name, event in engine.sample(sample(), time.monotonic_ns() // 1_000_000):
                print(f'{name}: {event}', flush=True)
            time.sleep(cfg.poll_interval_ms / 1000)
    except KeyboardInterrupt:
        pass
    finally:
        GPIO.cleanup()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config', type=Path, default=DEFAULT_CONFIG)
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument('--gpio', action='store_true')
    group.add_argument('--simulate', type=Path, metavar='JSONL')
    args = p.parse_args()
    cfg = load_config(args.config)
    if args.simulate:
        run_sim(cfg, args.simulate)
    else:
        run_gpio(cfg)


if __name__ == '__main__':
    main()
