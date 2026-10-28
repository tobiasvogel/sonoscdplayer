"""M0.14c standalone manual SSD1327 contrast test, no player access.

Use --gpio --execute for hardware; otherwise prints the proposed sequence only.
"""
from __future__ import annotations
import argparse
import json
import time
from .ssd1327 import DisplayConfig, Framebuffer, SSD1327

DEFAULT_LEVELS = (16, 32, 64, 96, 128, 160, 192, 224)

def parse_levels(value):
    try:
        levels = tuple(int(piece.strip()) for piece in value.split(','))
    except ValueError as exc:
        raise ValueError('contrast levels must be comma-separated integers') from exc
    if not levels or len(levels) > 32 or any(not 0 <= level <= 255 for level in levels):
        raise ValueError('provide 1..32 contrast levels in range 0..255')
    return levels

def main(argv=None):
    p=argparse.ArgumentParser(description='M0.14c manual SSD1327 contrast experiment')
    p.add_argument('--gpio',action='store_true')
    p.add_argument('--execute',action='store_true')
    p.add_argument('--spi-hz',type=int,default=2_000_000)
    p.add_argument('--levels',default=','.join(map(str,DEFAULT_LEVELS)),
                   help='comma-separated SSD1327 contrast values 0..255')
    p.add_argument('--seconds',type=float,default=3.0,help='time per contrast level')
    p.add_argument('--pattern',choices=['gray','text','grid','checker'],default='gray')
    a=p.parse_args(argv)
    if a.gpio != a.execute: p.error('--gpio and --execute must be given together')
    if not 0 < a.seconds <= 60: p.error('--seconds must be >0 and <=60')
    try: levels=parse_levels(a.levels)
    except ValueError as exc: p.error(str(exc))
    cfg=DisplayConfig(spi_hz=a.spi_hz,contrast=levels[0])
    display=SSD1327(cfg) if a.execute else None
    try:
        if display:
            display.open()
            display.show(Framebuffer().test_image(a.pattern))
        for level in levels:
            if display: display.set_contrast(level)
            print(json.dumps({'contrast':level,'pattern':a.pattern,'hardware':a.execute}),flush=True)
            if display: time.sleep(a.seconds)
    finally:
        if display: display.close()

if __name__=='__main__': main()
