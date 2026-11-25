"""128x128 image fitting and output packing, independent of the UI layout."""
from PIL import Image, ImageOps


def fitted_rgb(image):
    """Fit an image on a black 128x128 canvas, compositing transparency."""
    if not 1 <= image.width <= 4096 or not 1 <= image.height <= 4096:
        raise ValueError('invalid image dimensions')
    rgba = image.convert('RGBA')
    rgba = Image.alpha_composite(Image.new('RGBA', rgba.size, (0, 0, 0, 255)), rgba)
    fitted = ImageOps.contain(rgba.convert('RGB'), (128, 128),
                             method=Image.Resampling.LANCZOS)
    canvas = Image.new('RGB', (128, 128), 'black')
    canvas.paste(fitted, ((128-fitted.width)//2, (128-fitted.height)//2))
    return canvas


def image_to_frame(image, *, color=False):
    """Pack the shared canvas as RGB565 or the established grayscale frame."""
    if color:
        from sonoscdplayer.hardware.displays.rgb565 import image_to_frame as pack
        return pack(fitted_rgb(image), (128, 128))
    # Preserve the established grayscale-before-resampling output exactly.
    from sonoscdplayer.hardware.displays.ssd1327 import Framebuffer
    if not 1 <= image.width <= 4096 or not 1 <= image.height <= 4096:
        raise ValueError('invalid image dimensions')
    rgba = image.convert('RGBA')
    mono = Image.alpha_composite(Image.new('RGBA', rgba.size, (0, 0, 0, 255)), rgba).convert('L')
    fitted = ImageOps.contain(mono, (128, 128), method=Image.Resampling.LANCZOS)
    canvas = Image.new('L', (128, 128), 0)
    canvas.paste(fitted, ((128-fitted.width)//2, (128-fitted.height)//2))
    pixels = canvas.tobytes()
    frame = Framebuffer()
    frame.data[:] = bytes((((pixels[i]*15+127)//255) << 4) |
                          ((pixels[i+1]*15+127)//255) for i in range(0, len(pixels), 2))
    return frame
