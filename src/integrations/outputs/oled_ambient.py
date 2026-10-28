"""M0.14c-r1: optional APDS9306-controlled SSD1327 contrast.

Own, independent lux curve; no Sonos or LED control. The sensor is accessed
only through the existing read-only Unix socket protocol.
"""
from __future__ import annotations
from dataclasses import dataclass
import math
from .ambient_brightness import AmbientConfig, read_lux

CURVE = ((0.0,20),(1.0,32),(5.0,64),(18.0,128),
         (50.0,176),(100.0,208),(200.0,224))


def contrast_for_lux(lux: float) -> int:
    """Piecewise interpolation in log(1+lux); exact at measured calibration points."""
    if not math.isfinite(lux) or lux < 0:
        raise ValueError('lux must be finite and nonnegative')
    if lux <= CURVE[0][0]: return CURVE[0][1]
    for (low, a), (high, b) in zip(CURVE,CURVE[1:]):
        if lux <= high:
            fraction=(math.log1p(lux)-math.log1p(low))/(math.log1p(high)-math.log1p(low))
            return round(a+(b-a)*fraction)
    return CURVE[-1][1]


@dataclass(frozen=True)
class OLEDAmbientConfig:
    socket_path: str = '/run/apds9306.sock'
    timeout_ms: int = 150
    poll_ms: int = 1000
    stale_ms: int = 5000
    smoothing_ms: int = 2000
    fallback_contrast: int = 128

    def __post_init__(self):
        # Reuse input validation and socket protocol from the existing sensor module.
        AmbientConfig(socket_path=self.socket_path,timeout_ms=self.timeout_ms,
                      poll_ms=self.poll_ms,stale_ms=self.stale_ms,
                      smoothing_ms=self.smoothing_ms)
        if type(self.fallback_contrast) is not int or not 0 <= self.fallback_contrast <= 255:
            raise ValueError('fallback contrast must be 0..255')


class OLEDContrastControl:
    """On transient failures retain the last lux target; after stale_ms use fallback.

    clock argument is monotonic milliseconds, supplied by the caller. Never blocks
    more than sensor timeout for a poll; errors only affect OLED contrast.
    """
    def __init__(self, config: OLEDAmbientConfig, reader=None):
        self.cfg=config
        self.reader=reader or self._read
        self.value=float(config.fallback_contrast)
        self.target=float(config.fallback_contrast)
        self.last_update_ms=None
        self.last_poll_ms=None
        self.last_good_ms=None

    def _read(self):
        c=self.cfg
        return read_lux(AmbientConfig(socket_path=c.socket_path,timeout_ms=c.timeout_ms,
                                     poll_ms=c.poll_ms,stale_ms=c.stale_ms,
                                     smoothing_ms=c.smoothing_ms))

    def tick(self, now_ms: float) -> int:
        if not math.isfinite(now_ms): raise ValueError('invalid clock')
        if self.last_update_ms is not None and now_ms < self.last_update_ms:
            raise ValueError('clock moved backwards')
        if self.last_poll_ms is None or now_ms-self.last_poll_ms>=self.cfg.poll_ms:
            self.last_poll_ms=now_ms
            try:
                self.target=float(contrast_for_lux(self.reader()))
                self.last_good_ms=now_ms
            except (OSError,ValueError,TypeError,OverflowError):
                pass
        if self.last_good_ms is None or now_ms-self.last_good_ms >= self.cfg.stale_ms:
            self.target=float(self.cfg.fallback_contrast)
        elapsed=0 if self.last_update_ms is None else now_ms-self.last_update_ms
        self.last_update_ms=now_ms
        alpha=1.0 if self.cfg.smoothing_ms==0 else -math.expm1(-elapsed/self.cfg.smoothing_ms)
        self.value += alpha*(self.target-self.value)
        return max(0,min(255,round(self.value)))
