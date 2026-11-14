"""用标准库生成多尺寸网络节点图标，不需要 Pillow 或外部素材。"""
from pathlib import Path
import math
import struct
import zlib


def chunk(name, data):
    return struct.pack('!I', len(data)) + name + data + struct.pack('!I', zlib.crc32(name + data) & 0xffffffff)


def make_png(size):
    data = bytearray()
    for y in range(size):
        data.append(0)
        for x in range(size):
            px, py = (x + .5) / size, (y + .5) / size
            corner = math.hypot(max(.17 - px, px - .83, 0), max(.17 - py, py - .83, 0))
            alpha = 255 if corner < .15 else 0
            color = (16, int(43 + py * 30), int(78 + py * 35), alpha)
            node = any(abs(px - nx) < .1 and abs(py - ny) < .09 for nx, ny in ((.5, .29), (.27, .7), (.73, .7)))
            line = ((abs(px - .5) < .022 and .36 < py < .53)
                    or (.25 < px < .75 and abs(py - .52) < .022)
                    or (any(abs(px - nx) < .022 for nx in (.27, .73)) and .5 < py < .63))
            if node:
                color = (54, 214, 172, 255)
            elif line:
                color = (225, 240, 255, 255)
            data.extend(color)
    header = struct.pack('!2I5B', size, size, 8, 6, 0, 0, 0)
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', header) + chunk(b'IDAT', zlib.compress(bytes(data), 9)) + chunk(b'IEND', b'')


def make_icon(path):
    sizes = (16, 24, 32, 48, 64, 128, 256)
    pngs = [make_png(size) for size in sizes]
    header = struct.pack('<3H', 0, 1, len(sizes))
    offset = 6 + 16 * len(sizes)
    entries = bytearray()
    for size, png in zip(sizes, pngs):
        entries.extend(struct.pack('<4B2H2I', size % 256, size % 256, 0, 0, 1, 32, len(png), offset))
        offset += len(png)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(header + entries + b''.join(pngs))


if __name__ == '__main__':
    make_icon(Path(__file__).resolve().parents[1] / 'assets/app.ico')
