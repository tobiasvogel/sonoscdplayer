"""Optional passive player timing diagnostics; no network operations."""
from contextvars import ContextVar
from functools import wraps
from importlib.metadata import version
import time


# Only fixed labels may reach the journal, never request arguments or raw text.
_COMMANDS = frozenset(('play', 'play-selected', 'playpause', 'pause', 'resume',
                       'next', 'previous', 'stop', 'reset', 'eject',
                       'sonos-status', 'set-target', 'get-target', 'clear-target',
                       'inspect-group-recovery', 'resolve-group-recovery', 'help'))


def _log(message):
    try:
        print(message, flush=True)
    except OSError:
        # A failed diagnostic write must not alter a command's outcome.
        pass


# Scoped to the measured worker operation; never shared across threads.
_active_timing = ContextVar('active_network_timing', default=None)


def measure_network(operation, action):
    """Measure an inner operation only inside an enabled worker measurement."""
    timing = _active_timing.get()
    return action() if timing is None else timing.measure(operation, action)


def timed_network(operation):
    """Instrument a method without changing its callers or arguments."""
    def decorate(action):
        @wraps(action)
        def wrapped(*args, **kwargs):
            return measure_network(operation, lambda: action(*args, **kwargs))
        return wrapped
    return decorate


def read_grace_event(operation, started, *, now=None):
    """Report a grace transition, independently of the duration threshold.

    The caller supplies the existing monotonic start/deadline observation.
    Recovery needs a fresh diagnostic timestamp only when timing is enabled.
    """
    timing = _active_timing.get()
    if timing is None:
        return
    elapsed = (timing._clock() if now is None else now) - started
    _log(f'[sonos-timing] operation={operation} elapsed_seconds={elapsed:.3f}')


class TimingDiagnostics:
    def __init__(self, enabled=False, threshold=1.0, *, clock=time.monotonic):
        self.enabled = enabled
        self.threshold = threshold
        self._clock = clock

    @classmethod
    def from_config(cls, settings):
        sonos = settings.get('sonos', {})
        if not isinstance(sonos, dict):
            raise ValueError('[sonos] must be a table')
        enabled = sonos.get('timing_diagnostics', False)
        threshold = sonos.get('timing_threshold_seconds', 1.0)
        if type(enabled) is not bool:
            raise ValueError('sonos.timing_diagnostics must be true or false')
        if type(threshold) not in (int, float) or not 0 <= threshold <= 60:
            raise ValueError('sonos.timing_threshold_seconds must be a number in 0..60')
        return cls(enabled, float(threshold))

    def log_startup(self, timeout):
        """Report the applied timeout, without importing or contacting SoCo."""
        if timeout is not None:
            try:
                installed = version('soco')
            except Exception:
                installed = 'unknown'
            _log(f'[sonos] version={installed} request_timeout_seconds={timeout:g}')
        _log(f'[sonos-timing] enabled={self.enabled} threshold_seconds={self.threshold:g}')

    def measure(self, operation, action, *, command=None):
        """Call once and preserve results/exceptions; log only slow calls or failures.

        Operation and command labels must be fixed strings from our code.
        A returned refresh is not proof of network success.
        """
        if not self.enabled:
            return action()
        started = self._clock()
        outcome = 'returned'
        token = _active_timing.set(self)
        try:
            result = action()
            if command is not None:
                outcome = ('rejected' if isinstance(result, dict)
                           and result.get('ok') is False else 'ok')
            return result
        except BaseException as exc:
            outcome = type(exc).__name__
            raise
        finally:
            _active_timing.reset(token)
            elapsed = self._clock() - started
            if elapsed >= self.threshold or outcome not in ('returned', 'ok'):
                label = f' command={command}' if command is not None else ''
                _log(f'[sonos-timing] operation={operation}{label} '
                     f'elapsed_seconds={elapsed:.3f} outcome={outcome}')

    def command(self, action, request):
        if not self.enabled:
            return action(request)
        raw = request.get('command') if isinstance(request, dict) else None
        name = raw.strip().lower() if isinstance(raw, str) else ''
        label = name if name in _COMMANDS else 'other'
        return self.measure('command', lambda: action(request), command=label)
