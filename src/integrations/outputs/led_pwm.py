"""M0.13b opt-in PWM output. No Sonos write operations or audio dependencies.

RPi.GPIO.PWM is *software timed PWM*, even on hardware-PWM-capable BCM12/13.
A future pigpio/backend can supply DMA/hardware timing without changing this model.
"""
from __future__ import annotations
import argparse
import json
import math
import signal
import socket
import time
from concurrent.futures import ThreadPoolExecutor
from .ambient_brightness import AmbientConfig, read_lux, lux_to_brightness, BrightnessSmoother
from dataclasses import dataclass
from pathlib import Path

from .led_model import Connection, LedState, Led, Pattern, derive_led_state
from .led_patterns import PatternConfig, ErrorHold, intensity


@dataclass(frozen=True)
class OutputConfig:
    power_pin: int = 12
    connection_pin: int = 13
    pwm_hz: int = 200
    tick_ms: int = 25
    brightness: float = 0.7
    active_high: bool = True
    error_hold_ms: int = 3000
    ambient: AmbientConfig = AmbientConfig()

    def __post_init__(self):
        if type(self.power_pin) is not int or type(self.connection_pin) is not int:
            raise ValueError('pins must be integers')
        if (self.power_pin, self.connection_pin) != (12, 13):
            raise ValueError('M0.13b is restricted to reserved BCM12/13 outputs')
        if type(self.pwm_hz) is not int or not 50 <= self.pwm_hz <= 1000:
            raise ValueError('pwm_hz outside 50..1000')
        if type(self.tick_ms) is not int or not 10 <= self.tick_ms <= 250:
            raise ValueError('tick_ms outside 10..250')
        if type(self.brightness) not in (int, float) or not math.isfinite(self.brightness) or not 0 <= self.brightness <= 1:
            raise ValueError('brightness outside 0..1')
        if type(self.active_high) is not bool:
            raise ValueError('active_high must be boolean')
        if type(self.error_hold_ms) is not int or not 0 <= self.error_hold_ms <= 60000:
            raise ValueError('error_hold_ms outside 0..60000')


def load_config(path: str) -> OutputConfig:
    import tomllib
    with open(path, 'rb') as f:
        doc = tomllib.load(f)
    section = doc.get('led_outputs', {})
    if not isinstance(section, dict):
        raise ValueError('led_outputs must be a TOML table')
    ambient = section.pop('ambient', {})
    if not isinstance(ambient, dict): raise ValueError('led_outputs.ambient must be a TOML table')
    extra = set(ambient) - set(AmbientConfig.__dataclass_fields__)
    if extra: raise ValueError('unknown ambient keys: '+','.join(sorted(extra)))
    allowed = set(OutputConfig.__dataclass_fields__) - {'ambient'}
    unknown = set(section) - allowed
    if unknown:
        raise ValueError('unknown led_outputs keys: ' + ','.join(sorted(unknown)))
    return OutputConfig(**section, ambient=AmbientConfig(**ambient))


def read_status(socket_path: str, timeout: float = 0.35) -> dict:
    """Read-only `status` via local owner-only control socket; never issue PLAY/STOP."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(timeout)
        client.connect(socket_path)
        client.sendall(b'{"command":"status"}\n')
        data = bytearray()
        while b'\n' not in data and len(data) < 8192:
            piece = client.recv(2048)
            if not piece:
                break
            data.extend(piece)
    if b'\n' not in data:
        raise ValueError('incomplete status reply')
    result = json.loads(data.split(b'\n', 1)[0])
    if not result.get('ok') or not isinstance(result.get('state'), dict):
        raise ValueError('status unavailable')
    return result['state']


class PwmOutputs:
    """Owns ONLY BCM12/13, releases and drives off on shutdown.

    `gpio_module` can be a fake in tests. Set-up failure also cleans up pins.
    """
    def __init__(self, config: OutputConfig, gpio_module=None):
        self.config = config
        self.gpio = gpio_module
        self._pwm = []
        self._initialized = False

    def open(self):
        if self._initialized:
            raise RuntimeError('PWM already opened')
        if self.gpio is None:
            import RPi.GPIO as GPIO
            self.gpio = GPIO
        g = self.gpio
        try:
            g.setmode(g.BCM)
            for pin in (self.config.power_pin, self.config.connection_pin):
                g.setup(pin, g.OUT, initial=g.LOW if self.config.active_high else g.HIGH)
                p = g.PWM(pin, self.config.pwm_hz)
                p.start(0 if self.config.active_high else 100)
                self._pwm.append(p)
            self._initialized = True
        except BaseException:
            self.close()
            raise
        return self

    def write(self, power: float, connection: float):
        if not self._initialized:
            raise RuntimeError('PWM not opened')
        for p, amount in zip(self._pwm, (power, connection)):
            if not math.isfinite(amount) or not 0 <= amount <= 1:
                raise ValueError('duty outside 0..1')
            p.ChangeDutyCycle((amount if self.config.active_high else 1-amount)*100)

    def close(self):
        for p in self._pwm:
            try:
                p.ChangeDutyCycle(0 if self.config.active_high else 100)
                p.stop()
            except Exception:
                pass
        self._pwm.clear()
        if self.gpio is not None:
            for pin in (self.config.power_pin, self.config.connection_pin):
                try:
                    self.gpio.output(pin, self.gpio.LOW if self.config.active_high else self.gpio.HIGH)
                except Exception:
                    pass
            try:
                self.gpio.cleanup((self.config.power_pin, self.config.connection_pin))
            except Exception:
                pass
        self._initialized = False

    def __enter__(self):
        return self.open()

    def __exit__(self, *_):
        self.close()


class LedRenderer:
    def __init__(self, config: OutputConfig):
        self.config = config
        self.pattern_config = PatternConfig(brightness=config.brightness, error_hold_ms=config.error_hold_ms)
        self.hold = ErrorHold(self.pattern_config)
        self._last = (None, None)
        self._since = [0.0, 0.0]

    def tick(self, state: LedState, now_ms: float, brightness: float | None = None):
        state = self.hold.update(state, now_ms)
        if brightness is not None and (not math.isfinite(brightness) or not 0<=brightness<=1): raise ValueError('invalid brightness')
        result = []
        for i, led in enumerate((state.power, state.connection)):
            identity = (led.pattern, led.reason)
            if identity != self._last[i]:
                self._since[i] = now_ms
                old = list(self._last)
                old[i] = identity
                self._last = tuple(old)
            base = intensity(led.pattern, now_ms-self._since[i], self.pattern_config)
            result.append(base if brightness is None or self.config.brightness == 0 else base * brightness / self.config.brightness)
        return tuple(result)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Read-only LED PWM monitor (BCM12/13). Dry-run by default.')
    parser.add_argument('--config', default='config.toml')
    parser.add_argument('--socket', default='/tmp/sonoscdplayer-control.sock')
    parser.add_argument('--gpio', action='store_true', help='explicitly enable GPIO backend')
    parser.add_argument('--execute', action='store_true', help='required with --gpio for real output')
    parser.add_argument('--seconds', type=float, default=10.0, help='0 for continuous; default 10s')
    parser.add_argument('--ambient-probe', action='store_true', help='read sensor once, no GPIO/Player')
    parser.add_argument('--self-test', action='store_true', help='run pin/pattern checks without service or Sonos')
    parser.add_argument('--verbose', action='store_true', help='print status source and PWM values even with GPIO')
    parser.add_argument('--connection', choices=[c.value for c in Connection], default='unknown',
                        help='diagnostic observation ONLY; does not query Sonos reachability')
    args = parser.parse_args(argv)
    if args.execute != args.gpio:
        parser.error('real GPIO output requires BOTH --gpio and --execute')
    if not math.isfinite(args.seconds) or args.seconds < 0:
        parser.error('seconds must be finite and nonnegative')
    cfg = load_config(args.config)
    if args.ambient_probe:
        try:
            measured = read_lux(cfg.ambient)
            print(json.dumps({'lux': measured, 'target_brightness': round(lux_to_brightness(measured,cfg.ambient),4)}))
            return 0
        except (OSError, ValueError, UnicodeError) as exc:
            print(json.dumps({'sensor_error': type(exc).__name__+': '+str(exc), 'fallback_brightness': cfg.ambient.fallback_brightness}))
            return 1
    renderer = LedRenderer(cfg)
    smoother = BrightnessSmoother(cfg.ambient)
    # Poll in a worker, never delay the PWM refresh or status polling on a sensor socket.
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='ambient-lux') if cfg.ambient.enabled and not args.self_test else None
    sensor_future = None
    next_sensor_poll = 0.0
    last_lux = None
    last_lux_at = None
    last_sensor_error = None
    from contextlib import nullcontext
    backend = PwmOutputs(cfg) if args.gpio else None
    context = backend if backend is not None else nullcontext()
    started = time.monotonic()
    deadline = started+args.seconds if args.seconds else None
    last_status = None
    last_good_at = None
    last_error = None
    next_poll = 0.0
    last_print = None
    try:
        with context:
            while deadline is None or time.monotonic() < deadline:
                now = time.monotonic()
                if args.self_test:
                    # Each stage drives a unique, known pair of patterns. Neither
                    # status socket nor Sonos is accessed in this diagnostic mode.
                    stage = int((now - started) // 4) % 7
                    pairs = (
                        (Pattern.STEADY, Pattern.OFF),
                        (Pattern.OFF, Pattern.STEADY),
                        (Pattern.SLOW_BLINK, Pattern.OFF),
                        (Pattern.OFF, Pattern.DOUBLE_FLASH),
                        (Pattern.OFF, Pattern.TRIPLE_FLASH),
                        (Pattern.BREATHE, Pattern.BREATHE),
                        (Pattern.STEADY, Pattern.STEADY),
                    )
                    power_pattern, connection_pattern = pairs[stage]
                    state = LedState(Led(power_pattern, 'self_test'), Led(connection_pattern, 'self_test'))
                    source = 'self_test_stage_' + str(stage + 1)
                else:
                    if now >= next_poll:
                        try:
                            last_status = read_status(args.socket)
                            last_good_at = time.monotonic()
                            last_error = None
                        except (OSError, ValueError, json.JSONDecodeError) as exc:
                            # Single-threaded player_service can block status
                            # requests while waiting for CD spin-up (<=45s).
                            # Do not turn that transient into a false LED fault.
                            busy = isinstance(exc, (BlockingIOError, TimeoutError, socket.timeout))
                            grace = 50.0 if busy else 4.0
                            if last_good_at is None or time.monotonic() - last_good_at >= grace:
                                last_status = None
                            last_error = type(exc).__name__ + ': ' + str(exc)
                        next_poll = now + 0.5
                    if last_status is None:
                        # Make socket failure visible instead of silently masking
                        # caller-selected connection simulations.
                        state = derive_led_state({'schema_version': 1, 'player': {'mode': 'ERROR'}},
                                                 connection=Connection(args.connection))
                        source = 'status_unavailable: ' + str(last_error)
                    else:
                        try:
                            state = derive_led_state(last_status, connection=Connection(args.connection))
                            source = 'player_status'
                        except ValueError as exc:
                            state = derive_led_state({'schema_version': 1, 'player': {'mode': 'ERROR'}},
                                                     connection=Connection(args.connection))
                            source = 'status_invalid: ' + str(exc)
                if executor is not None:
                    if sensor_future is not None and sensor_future.done():
                        try:
                            last_lux = sensor_future.result()
                            last_lux_at = now
                            last_sensor_error = None
                        except (OSError, ValueError, UnicodeError) as exc:
                            last_sensor_error = type(exc).__name__+': '+str(exc)
                            last_lux = None
                        sensor_future = None
                    if now >= next_sensor_poll and sensor_future is None:
                        sensor_future = executor.submit(read_lux, cfg.ambient)
                        next_sensor_poll = now + cfg.ambient.poll_ms/1000
                current_brightness = None
                if executor is not None:
                    valid = last_lux is not None and last_lux_at is not None and (now-last_lux_at)*1000 <= cfg.ambient.stale_ms
                    target = lux_to_brightness(last_lux,cfg.ambient) if valid else cfg.ambient.fallback_brightness
                    current_brightness = smoother.tick(target,now*1000)
                duties = renderer.tick(state, now*1000, brightness=current_brightness)
                if backend is not None:
                    backend.write(*duties)
                if backend is None or args.verbose or args.self_test:
                    summary = (source, state.power.pattern.value, state.connection.pattern.value,
                               round(duties[0], 2), round(duties[1], 2))
                    # Avoid flooding on normal PWM ticks, but preserve transitions.
                    if summary != last_print and (args.verbose or args.self_test or backend is None):
                        print(json.dumps({'source': summary[0], 'power_pattern': summary[1],
                                          'connection_pattern': summary[2], 'power_duty': summary[3],
                                          'connection_duty': summary[4],
                                          'lux': last_lux if executor is not None else None,
                                          'ambient_error': last_sensor_error if executor is not None else None}), flush=True)
                        last_print = summary
                time.sleep(cfg.tick_ms/1000)
    except KeyboardInterrupt:
        pass
    finally:
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
