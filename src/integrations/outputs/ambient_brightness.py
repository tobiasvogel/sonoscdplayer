"""M0.13c optional APDS9306 lux reader and smooth brightness control.

This module never touches I2C, Sonos or GPIO. Sensor access occurs only
on an opt-in, short-timeout Unix socket read. Unknown formats fail closed.
"""
from __future__ import annotations
import re
import math
import socket
from dataclasses import dataclass

@dataclass(frozen=True)
class AmbientConfig:
    enabled: bool = False
    socket_path: str = '/run/apds9306.sock'
    request: str = ''  # optional documented daemon command, terminated by LF
    timeout_ms: int = 150
    poll_ms: int = 1000
    min_brightness: float = 0.08
    max_brightness: float = 0.85
    fallback_brightness: float = 0.7
    lux_dark: float = 1.0
    lux_bright: float = 300.0
    smoothing_ms: int = 2000
    stale_ms: int = 5000

    def __post_init__(self):
        if type(self.enabled) is not bool: raise ValueError('enabled must be boolean')
        if not isinstance(self.socket_path,str) or not self.socket_path.startswith('/') or '\x00' in self.socket_path: raise ValueError('socket_path must be absolute')
        if not isinstance(self.request,str) or '\n' in self.request or '\r' in self.request or len(self.request)>128 or not self.request.isascii(): raise ValueError('invalid request')
        for k,lo,hi in [('timeout_ms',10,1000),('poll_ms',100,60000),('smoothing_ms',0,60000),('stale_ms',100,120000)]:
            v=getattr(self,k)
            if type(v) is not int or not lo<=v<=hi: raise ValueError(k+' invalid')
        if self.stale_ms < self.poll_ms: raise ValueError('stale_ms must be >= poll_ms')
        for k in ('min_brightness','max_brightness','fallback_brightness'):
            v=getattr(self,k)
            if type(v) not in (float,int) or not math.isfinite(v) or not 0<=v<=1: raise ValueError(k+' must be 0..1')
        if self.min_brightness>self.max_brightness: raise ValueError('min brightness exceeds max')
        for k in ('lux_dark','lux_bright'):
            v=getattr(self,k)
            if type(v) not in (float,int) or not math.isfinite(v) or v<0: raise ValueError(k+' invalid')
        if self.lux_bright<=self.lux_dark: raise ValueError('lux_bright must exceed lux_dark')


_DECIMAL_LUX = re.compile(r"(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)")


def parse_lux(raw: bytes) -> float:
    """Parse the APDS9306 daemon's single unitless, nonnegative decimal lux value.

    Whitespace (including a trailing newline) is permitted; JSON, units,
    exponent notation, NaN/Inf, and multi-value replies are rejected.
    """
    if len(raw) > 2048:
        raise ValueError('sensor reply too long')
    text = raw.decode('ascii', errors='strict').strip()
    if not _DECIMAL_LUX.fullmatch(text):
        raise ValueError('sensor reply must be a single decimal lux number')
    value = float(text)
    if not math.isfinite(value):
        raise ValueError('invalid lux reading')
    return value


def read_lux(cfg: AmbientConfig) -> float:
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as sock:
        sock.settimeout(cfg.timeout_ms/1000)
        sock.connect(cfg.socket_path)
        if cfg.request: sock.sendall((cfg.request+'\n').encode('ascii'))
        chunks=bytearray()
        while len(chunks)<=2048 and b'\n' not in chunks:
            part=sock.recv(min(512,2049-len(chunks)))
            if not part: break
            chunks.extend(part)
    return parse_lux(bytes(chunks).split(b'\n',1)[0])


def lux_to_brightness(lux: float,cfg: AmbientConfig) -> float:
    if not math.isfinite(lux) or lux<0: raise ValueError('invalid lux')
    # Logarithmic response better matches visual perception; clamp to endpoints.
    position=(math.log1p(lux)-math.log1p(cfg.lux_dark))/(math.log1p(cfg.lux_bright)-math.log1p(cfg.lux_dark))
    position=min(1.0,max(0.0,position))
    return cfg.min_brightness+(cfg.max_brightness-cfg.min_brightness)*position

class BrightnessSmoother:
    def __init__(self,cfg:AmbientConfig):
        self.cfg=cfg
        self.value=float(cfg.fallback_brightness)
        self.last_ms=None
    def tick(self,target:float,now_ms:float)->float:
        if not 0<=target<=1 or not math.isfinite(target) or not math.isfinite(now_ms): raise ValueError('invalid target/time')
        if self.last_ms is not None and now_ms<self.last_ms: raise ValueError('clock moved backwards')
        dt=0 if self.last_ms is None else now_ms-self.last_ms
        self.last_ms=now_ms
        alpha=1.0 if self.cfg.smoothing_ms==0 else 1-math.exp(-dt/self.cfg.smoothing_ms)
        self.value+=(target-self.value)*alpha
        return self.value
