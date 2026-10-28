"""Pure monotonic-time PWM pattern math and optional error display hold. No GPIO.

Elapsed time is supplied by the caller. No sleeps, threads or I/O.
"""
from __future__ import annotations
from dataclasses import dataclass
from math import cos, pi, isfinite
from .led_model import Pattern, LedState, Led

@dataclass(frozen=True)
class PatternConfig:
    brightness: float = 0.7
    slow_on_ms: int = 500
    slow_off_ms: int = 500
    fast_on_ms: int = 150
    fast_off_ms: int = 150
    flash_on_ms: int = 150
    flash_gap_ms: int = 150
    flash_period_ms: int = 2000
    breathe_period_ms: int = 2000
    error_hold_ms: int = 3000

    def __post_init__(self):
        if not isinstance(self.brightness, (int, float)) or not isfinite(self.brightness) or not 0 <= self.brightness <= 1:
            raise ValueError('brightness must be finite and between 0 and 1')
        for key in ('slow_on_ms','slow_off_ms','fast_on_ms','fast_off_ms','flash_on_ms','flash_gap_ms','flash_period_ms','breathe_period_ms'):
            value = getattr(self, key)
            if type(value) is not int or value <= 0:
                raise ValueError(key + ' must be positive integer milliseconds')
        if type(self.error_hold_ms) is not int or not 0 <= self.error_hold_ms <= 60000:
            raise ValueError('error_hold_ms out of range')
        if 3*self.flash_on_ms + 2*self.flash_gap_ms > self.flash_period_ms:
            raise ValueError('triple flash must fit within flash period')

def intensity(pattern: Pattern, elapsed_ms: float, config: PatternConfig = PatternConfig()) -> float:
    """Return normalized PWM duty cycle [0,1]; elapsed_ms must be monotonic elapsed time."""
    if not isinstance(pattern, Pattern):
        raise ValueError('unknown pattern')
    if not isinstance(elapsed_ms, (int, float)) or not isfinite(elapsed_ms) or elapsed_ms < 0:
        raise ValueError('elapsed_ms must be finite and nonnegative')
    if pattern == Pattern.OFF: return 0.0
    if pattern == Pattern.STEADY: return float(config.brightness)
    if pattern == Pattern.BREATHE:
        return float(config.brightness) * (0.1 + 0.9*(1-cos(2*pi*(elapsed_ms % config.breathe_period_ms)/config.breathe_period_ms))/2)
    if pattern in (Pattern.BLINK, Pattern.SLOW_BLINK, Pattern.FAST_BLINK):
        on, off = (config.fast_on_ms, config.fast_off_ms) if pattern == Pattern.FAST_BLINK else (config.slow_on_ms, config.slow_off_ms)
        return float(config.brightness) if elapsed_ms % (on+off) < on else 0.0
    if pattern in (Pattern.DOUBLE_FLASH, Pattern.TRIPLE_FLASH):
        phase = elapsed_ms % config.flash_period_ms
        count = 2 if pattern == Pattern.DOUBLE_FLASH else 3
        return float(config.brightness) if any(i*(config.flash_on_ms+config.flash_gap_ms) <= phase < i*(config.flash_on_ms+config.flash_gap_ms)+config.flash_on_ms for i in range(count)) else 0.0
    raise ValueError('unsupported pattern')

_ERROR_REASONS = frozenset({'critical_device_fault','drive_fault','player_error_unclassified','target_unreachable','unsafe_sonos_topology','stream_ownership_lost'})
_PRIORITY = {'critical_device_fault': 5, 'drive_fault':4, 'player_error_unclassified':3,
             'stream_ownership_lost':5, 'unsafe_sonos_topology':4, 'target_unreachable':3}

class ErrorHold:
    """Per-LED minimum error visibility, explicit timestamps, no background thread.

    A higher-priority error overrides a held lower-priority error. Shutdown/off
    bypasses the hold. The hold never changes player state or Sonos commands.
    """
    def __init__(self, config: PatternConfig = PatternConfig()):
        self.config = config
        self._active = [None, None]
        self._until = [0.0, 0.0]
        self._last_time = None

    def update(self, state: LedState, now_ms: float, *, operating: bool = True) -> LedState:
        if not isinstance(state, LedState) or not isinstance(operating, bool):
            raise ValueError('invalid state or operating flag')
        if not isinstance(now_ms, (int, float)) or not isfinite(now_ms) or now_ms < 0:
            raise ValueError('now_ms must be finite and nonnegative')
        if self._last_time is not None and now_ms < self._last_time:
            raise ValueError('time must be monotonic')
        self._last_time = now_ms
        if not operating:
            self._active = [None, None]
            self._until = [0.0, 0.0]
            return state
        result=[]
        for i, incoming in enumerate((state.power, state.connection)):
            old=self._active[i]
            is_error=incoming.reason in _ERROR_REASONS
            if is_error and (old is None or incoming.reason != old.reason and _PRIORITY[incoming.reason] >= _PRIORITY[old.reason]):
                old=incoming
                self._active[i]=incoming
                self._until[i]=now_ms+self.config.error_hold_ms
            elif is_error and old is not None and incoming.reason == old.reason:
                # Persistent errors remain visible, but do not extend a minimum hold indefinitely.
                old=incoming
                self._active[i]=incoming
            elif old is not None and now_ms >= self._until[i]:
                old=None
                self._active[i]=None
            result.append(old if old is not None else incoming)
        return LedState(*result)
