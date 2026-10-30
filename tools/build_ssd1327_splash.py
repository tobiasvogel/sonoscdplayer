#!/usr/bin/env python3
"""Convert PNG into Raspberry Pi splashasm source for project SSD1327 SPI pinout."""
import argparse
from pathlib import Path
from PIL import Image, ImageOps

INIT=[(0xfd,0x12),(0xae,),(0x15,0,63),(0x75,0,127),
      (0x81,128),(0xa0,0x51),(0xa1,0),(0xa2,0),(0xa4,),
      (0xa8,127),(0xab,1),(0xb1,0x11),(0xb3,0),(0xb9,),
      (0xbc,0x08),(0xbe,0x07),(0xb6,1),(0xd5,0x62),(0xaf,)]

def to_frame(image):
    if not 1 <= image.width <= 4096 or not 1 <= image.height <= 4096:
        raise ValueError('invalid input size')
    rgba=image.convert('RGBA')
    bg=Image.new('RGBA',rgba.size,(0,0,0,255))
    gray=Image.alpha_composite(bg,rgba).convert('L')
    resized=ImageOps.contain(gray,(128,128),method=Image.Resampling.LANCZOS)
    canvas=Image.new('L',(128,128),0)
    canvas.paste(resized,((128-resized.width)//2,(128-resized.height)//2))
    px=canvas.load()
    return bytes(((((px[x,y]*15+127)//255)<<4)|((px[x+1,y]*15+127)//255))
                 for y in range(128) for x in range(0,128,2))

def build(frame,*,hz=2_000_000,contrast=128,chunk=128):
    if len(frame)!=8192: raise ValueError('expected 8192 framebuffer bytes')
    if not 100000<=hz<=8000000 or not 0<=contrast<=255 or not 1<=chunk<=4096:
        raise ValueError('invalid splash parameters')
    lines=['# SSD1327 SPI0/CE0, DC25, RST24 MUST ALREADY BE HIGH',
           f'define oled spi [copi 10] [cipo 9] [sclk 11] [cs 8] [dc 25] [freq {hz}]']
    for command in INIT:
        vals=list(command)
        if vals[0]==0x81: vals[1]=contrast
        # SSD1327 command parameters must also be sent with D/C low.
        # SplashAsm changes D/C high after the first byte of a normal
        # transaction, so emit every command byte separately.
        lines.extend(f'oled 0x{v:02x}' for v in vals)
    lines += ['oled 0x15','oled 0x00','oled 0x3f',
              'oled 0x75','oled 0x00','oled 0x7f','oled 0x5c']
    for i in range(0,len(frame),chunk):
        lines.append('oled [DATA_ONLY] '+' '.join(f'0x{v:02x}' for v in frame[i:i+chunk]))
    return '\n'.join(lines)+'\n'

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('png',type=Path)
    p.add_argument('-o','--output',type=Path,default=Path('ssd1327.splash'))
    p.add_argument('--spi-hz',type=int,default=2_000_000)
    p.add_argument('--contrast',type=int,default=128)
    a=p.parse_args()
    with Image.open(a.png) as im:
        im.load()
        framebuffer=to_frame(im)
    a.output.write_text(build(framebuffer,hz=a.spi_hz,contrast=a.contrast),encoding='utf-8')
    print(f'Wrote {a.output}: {len(framebuffer)} framebuffer bytes')

if __name__=='__main__': main()
