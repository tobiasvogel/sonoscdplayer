#!/usr/bin/env python3
"""Build-only PNG conversion for the selected shutdown display backend.

The late-shutdown executable does not need Python, Pillow, or filesystem images.
"""
import argparse
from pathlib import Path
from PIL import Image, ImageOps


def pack_ssd1327(path: Path) -> bytes:
    with Image.open(path) as source:
        source.load()
        if not 0 < source.width <= 4096 or not 0 < source.height <= 4096:
            raise ValueError('invalid source image dimensions')
        rgba = source.convert('RGBA')
    backdrop = Image.new('RGBA', rgba.size, (0, 0, 0, 255))
    mono = Image.alpha_composite(backdrop, rgba).convert('L')
    fitted = ImageOps.contain(mono, (128, 128), method=Image.Resampling.LANCZOS)
    canvas = Image.new('L', (128, 128), 0)
    canvas.paste(fitted, ((128 - fitted.width) // 2, (128 - fitted.height) // 2))
    data = canvas.tobytes()
    return bytes((((data[i] * 15 + 127) // 255) << 4) |
                 ((data[i + 1] * 15 + 127) // 255)
                 for i in range(0, len(data), 2))


def generate(wait: Path, final: Path, reboot: Path, transition: Path, output: Path, backend: str) -> None:
    if backend != 'ssd1327':
        raise ValueError('unsupported backend: ' + backend)
    frames = {'wait': pack_ssd1327(wait), 'final': pack_ssd1327(final),
              'reboot': pack_ssd1327(reboot), 'transition': pack_ssd1327(transition)}
    output.parent.mkdir(parents=True, exist_ok=True)
    lines = ['/* Generated at build time; do not edit. GPL-2.0-only. */',
             '#ifndef SONOSCDPLAYER_SHUTDOWN_FRAMES_H',
             '#define SONOSCDPLAYER_SHUTDOWN_FRAMES_H',
             '#include <stdint.h>',
             '#define SHUTDOWN_FRAME_WIDTH 128u',
             '#define SHUTDOWN_FRAME_HEIGHT 128u',
             '#define SHUTDOWN_FRAME_FORMAT "gray4-packed"']
    for name, data in frames.items():
        lines.append(f'static const uint8_t shutdown_frame_{name}[{len(data)}] = {{')
        for offset in range(0, len(data), 16):
            lines.append('    ' + ', '.join(f'0x{b:02x}' for b in data[offset:offset + 16]) + ',')
        lines.append('};')
    lines.append('#endif')
    output.write_text('\n'.join(lines) + '\n', encoding='ascii')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--backend', choices=['ssd1327'], default='ssd1327')
    parser.add_argument('--wait', type=Path, required=True)
    parser.add_argument('--final', type=Path, required=True)
    parser.add_argument('--reboot', type=Path, required=True)
    parser.add_argument('--transition', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    generate(args.wait, args.final, args.reboot, args.transition, args.output, args.backend)


if __name__ == '__main__':
    main()
