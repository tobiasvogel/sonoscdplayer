"""Shared, hardware-free RGB565 packing and software rotation."""
from sonoscdplayer.ui.displays.backend import GraphicFrame, validate_frame
from sonoscdplayer.ui.displays.capabilities import DisplayCapabilities


def image_to_frame(image, size):
    """Pack row-major RGB565, red in bits 15..11, high byte first."""
    if image.size != size:
        raise ValueError('image does not match logical display size')
    width, height = size
    data = bytearray(width*height*2)
    pixels = image.convert('RGB').tobytes()
    for offset in range(0, len(pixels), 3):
        red, green, blue = pixels[offset:offset+3]
        value = ((red >> 3) << 11) | ((green >> 2) << 5) | (blue >> 3)
        index = (offset//3)*2
        data[index:index+2] = value.to_bytes(2, 'big')
    return GraphicFrame(width, height, 'rgb565', bytes(data))


def native_bytes(frame, *, native_size, rotation):
    """Validate and rotate a logical frame clockwise into native coordinates."""
    if type(rotation) is not int or rotation not in (0, 90, 180, 270):
        raise ValueError('rotation must be 0, 90, 180 or 270')
    width, height = native_size
    size = (height, width) if rotation in (90, 270) else native_size
    validate_frame(DisplayCapabilities(kind='graphic', width=size[0], height=size[1],
                                       pixel_format='rgb565'), frame)
    if rotation == 0:
        return frame.data
    result = bytearray(width*height*2)
    for y in range(frame.height):
        for x in range(frame.width):
            if rotation == 90:
                nx, ny = width-1-y, x
            elif rotation == 180:
                nx, ny = width-1-x, height-1-y
            else:
                nx, ny = y, height-1-x
            source, target = 2*(y*frame.width+x), 2*(ny*width+nx)
            result[target:target+2] = frame.data[source:source+2]
    return bytes(result)
