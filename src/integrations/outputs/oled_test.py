"""M0.14a standalone SSD1327 diagnostic, no player/Sonos access."""
from __future__ import annotations
import argparse
import hashlib
import json
import time
from .ssd1327 import DisplayConfig, Framebuffer, SSD1327, FRAME_BYTES


def main(argv=None):
    p=argparse.ArgumentParser(description='M0.14a SSD1327 display self-test')
    p.add_argument('--gpio',action='store_true',help='Use physical SPI and GPIO')
    p.add_argument('--execute',action='store_true',help='Required hardware authorization')
    p.add_argument('--spi-hz',type=int,default=2_000_000)
    p.add_argument('--contrast',type=int,default=128)
    p.add_argument('--seconds',type=float,default=3)
    p.add_argument('--pattern',choices=['all','gray','grid','checker','text','clear'],default='all')
    a=p.parse_args(argv)
    if a.execute != a.gpio:
        p.error('--gpio and --execute must be given together')
    if not (0 < a.seconds <= 60): p.error('--seconds must be >0 and <=60')
    config=DisplayConfig(spi_hz=a.spi_hz, contrast=a.contrast)
    patterns=['gray','grid','checker','text'] if a.pattern=='all' else [a.pattern]
    driver=SSD1327(config) if a.execute else None
    try:
        if driver: driver.open()
        for pat in patterns:
            framebuffer=Framebuffer().test_image(pat)
            print(json.dumps({'pattern':pat,'bytes':FRAME_BYTES,'sha256':hashlib.sha256(framebuffer.data).hexdigest()[:16], 'hardware':a.execute}),flush=True)
            if driver:
                driver.show(framebuffer)
                time.sleep(a.seconds)
    finally:
        if driver: driver.close()

if __name__=='__main__': main()
