#!/usr/bin/env python3
"""
CyrusAsha_GuiForOpenDataLoaderPDF - build the brand assets from the project logo.

Everything is derived from ONE source file, ``assets/CyrusAsha_GuiForOpenDataLoaderPDF-logo.png``
(the transparent CyrusAsha logo: eagle emblem + "CyrusAsha" wordmark + tagline):

* ``src/gui/public/brand/CyrusAsha_GuiForOpenDataLoaderPDF.png`` - emblem on a dark navy tile (header, 256 px)
* ``src/gui/public/brand/favicon.png``                     - emblem tile for the browser tab (64 px)
* ``src/gui/public/brand/logo-full.png``                   - full transparent logo for the Help page (512 px)
* ``windows/icon.ico``                                      - Windows icon with ten sizes (16 ... 256 px)

This is a developer tool: it needs Pillow (``pip install pillow``). The generated
files are committed, so end users never need Pillow.

Usage::

    python scripts/build-icons.py
"""

from __future__ import annotations

import io
import os
import struct
import sys

try:
    from PIL import Image, ImageDraw
except ImportError:  # pragma: no cover - developer tool
    sys.exit("Pillow is required: pip install pillow")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = os.path.join(ROOT, "assets", "CyrusAsha_GuiForOpenDataLoaderPDF-logo.png")
BRAND_DIR = os.path.join(ROOT, "src", "gui", "public", "brand")
ICON_PATH = os.path.join(ROOT, "windows", "icon.ico")

#: Icon sizes embedded in windows/icon.ico (same set as CyrusAsha_UniversalRTL).
ICO_SIZES = [16, 20, 24, 32, 40, 48, 64, 96, 128, 256]

#: Dark navy of the CyrusAsha emblem tile (matches the logo's night-sky look).
TILE_INNER = (14, 30, 52)
TILE_OUTER = (4, 10, 20)


def emblem_box(logo: Image.Image) -> tuple:
    """Find the bounding box of the eagle emblem (everything above the wordmark).

    The logo is scanned row by row; the first fully transparent band after the
    emblem separates it from the "CyrusAsha" wordmark.

    Args:
        logo: The RGBA logo.

    Returns:
        ``(left, top, right, bottom)`` of the emblem.
    """
    alpha = logo.getchannel("A")
    width, height = logo.size
    band = max(2, height // 512)
    seen_content = False
    bottom = height
    for y in range(0, height, band):
        row = alpha.crop((0, y, width, min(height, y + band)))
        opaque = max(row.getextrema()) > 40
        if opaque:
            seen_content = True
        elif seen_content:
            bottom = y
            break
    box = alpha.crop((0, 0, width, bottom)).point(lambda v: 255 if v > 40 else 0).getbbox()
    return box or (0, 0, width, bottom)


def radial_tile(size: int) -> Image.Image:
    """Create a square dark-navy tile with a soft radial glow (no transparency).

    Args:
        size: Edge length in pixels.

    Returns:
        RGBA image.
    """
    tile = Image.new("RGBA", (size, size), TILE_OUTER + (255,))
    draw = ImageDraw.Draw(tile)
    steps = 48
    for i in range(steps, 0, -1):
        t = i / steps
        color = tuple(int(TILE_OUTER[c] + (TILE_INNER[c] - TILE_OUTER[c]) * (1 - t)) for c in range(3))
        r = int(size * 0.75 * t)
        cx, cy = size // 2, int(size * 0.45)
        draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=color + (255,))
    return tile


def emblem_tile(logo: Image.Image, size: int, padding: float = 0.06) -> Image.Image:
    """Render the eagle emblem centred on a dark tile.

    Args:
        logo: The RGBA logo.
        size: Output edge length.
        padding: Free margin as a fraction of ``size``.

    Returns:
        RGBA image of ``size`` x ``size``.
    """
    emblem = logo.crop(emblem_box(logo))
    inner = int(size * (1 - 2 * padding))
    emblem.thumbnail((inner, inner), Image.LANCZOS)
    tile = radial_tile(size)
    tile.alpha_composite(emblem, ((size - emblem.width) // 2, (size - emblem.height) // 2))
    return tile


def save_png(image: Image.Image, path: str) -> None:
    """Save an optimised PNG and print its size.

    Args:
        image: Image to save.
        path: Target path.
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    image.save(path, "PNG", optimize=True)
    print(f"  {os.path.relpath(path, ROOT)}  {image.size[0]}x{image.size[1]}  {os.path.getsize(path) // 1024} KB")


def build_ico(logo: Image.Image, path: str) -> None:
    """Write a multi-size .ico whose frames are PNG-compressed (Windows Vista+).

    Small sizes use less padding so the eagle stays recognisable.

    Args:
        logo: The RGBA logo.
        path: Target .ico path.
    """
    frames = []
    for size in ICO_SIZES:
        tile = emblem_tile(logo, size, padding=0.02 if size <= 32 else 0.05)
        buf = io.BytesIO()
        tile.save(buf, "PNG", optimize=True)
        frames.append((size, buf.getvalue()))
    header = struct.pack("<HHH", 0, 1, len(frames))
    offset = 6 + 16 * len(frames)
    entries, blobs = b"", b""
    for size, png in frames:
        dim = 0 if size >= 256 else size
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(png), offset + len(blobs))
        blobs += png
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(header + entries + blobs)
    print(f"  {os.path.relpath(path, ROOT)}  {len(frames)} sizes ({ICO_SIZES[0]}-{ICO_SIZES[-1]})  {os.path.getsize(path) // 1024} KB")


def main() -> int:
    """Generate every brand asset from the logo.

    Returns:
        Process exit code.
    """
    if not os.path.isfile(SOURCE):
        print(f"Logo not found: {SOURCE}")
        return 1
    logo = Image.open(SOURCE).convert("RGBA")
    print(f"Source: {os.path.relpath(SOURCE, ROOT)} {logo.size[0]}x{logo.size[1]}")
    save_png(emblem_tile(logo, 256), os.path.join(BRAND_DIR, "CyrusAsha_GuiForOpenDataLoaderPDF.png"))
    save_png(emblem_tile(logo, 64, padding=0.03), os.path.join(BRAND_DIR, "favicon.png"))
    full = logo.copy()
    full.thumbnail((512, 512), Image.LANCZOS)
    save_png(full, os.path.join(BRAND_DIR, "logo-full.png"))
    build_ico(logo, ICON_PATH)
    return 0


if __name__ == "__main__":
    sys.exit(main())
