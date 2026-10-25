# SPDX-License-Identifier: Apache-2.0
"""Exact shared-palette planning and frame-major 4bpp verification, without quantization."""
import re
import struct
from pathlib import Path

from PIL import Image


def rgb(value: str) -> tuple[int, ...]:
    if not isinstance(value, str) or re.fullmatch(r"#[0-9a-fA-F]{6}", value) is None:
        raise ValueError("colors must be #rrggbb")
    return tuple(bytes.fromhex(value[1:]))


def hex_rgb(value) -> str:
    return "#" + bytes(value).hex()


def bgr555(color) -> int:
    """Require exact decoded channel values (floor(v * 255 / 31)), never round art."""
    levels = [v * 255 // 31 for v in range(32)]
    if len(color) != 3 or any(type(v) is not int or v not in levels for v in color):
        raise ValueError(f"RGB {color} is not exactly representable in BGR555")
    return sum(levels.index(v) << (5 * i) for i, v in enumerate(color))


def plan_palette(components: dict[str, list[str]], substitutions: dict | None = None) -> dict:
    """Union every palette consumer, including separately rendered components.

    Substitutions are explicit per-component RGB maps. Suggestions rank the
    squared RGB distance of single-color merges; they are never applied and
    cannot decide artistic similarity or approval.
    """
    if not isinstance(components, dict) or not components:
        raise ValueError("palette needs named components and their full opaque color lists")
    colors = {}
    for name, values in components.items():
        if not isinstance(name, str) or not name or not isinstance(values, list) or not values:
            raise ValueError("each palette component needs a name and nonempty color list")
        colors[name] = {rgb(v) for v in values}
    substitutions = {} if substitutions is None else substitutions
    if not isinstance(substitutions, dict) or not set(substitutions) <= set(colors):
        raise ValueError("substitutions must name existing components")
    mapped, changes = {}, []
    for name, values in colors.items():
        mapping = substitutions.get(name, {})
        if not isinstance(mapping, dict):
            raise ValueError("component substitutions must be RGB maps")
        pairs = {rgb(a): rgb(b) for a, b in mapping.items()}
        if len(pairs) != len(mapping) or not pairs.keys() <= values:
            raise ValueError("duplicate or absent substitution source RGB")
        mapped[name] = {pairs.get(c, c) for c in values}
        changes.extend({"component": name, "from": hex_rgb(a), "to": hex_rgb(b)}
                       for a, b in sorted(pairs.items()) if a != b)
    union = set().union(*mapped.values())
    invalid = []
    for c in sorted(union):
        try:
            bgr555(c)
        except ValueError:
            invalid.append(hex_rgb(c))
    suggestions = []
    if len(union) > 15:
        for name, values in mapped.items():
            others = set().union(*(v for n, v in mapped.items() if n != name))
            # A merge only saves a hardware slot if no other consumer uses it.
            for a in sorted(values - others):
                choices = union - {a}
                b = min(choices, key=lambda c: (sum((x-y)**2 for x, y in zip(a, c)), c))
                suggestions.append({"component": name, "from": hex_rgb(a), "to": hex_rgb(b),
                                    "squared_rgb_distance": sum((x-y)**2 for x, y in zip(a, b))})
    return {"ok": len(union) <= 15 and not invalid, "approved": False,
            "opaque_color_count": len(union), "opaque_budget": 15, "transparent_index": 0,
            "colors": [hex_rgb(c) for c in sorted(union)],
            "components": {n: [hex_rgb(c) for c in sorted(v)] for n, v in mapped.items()},
            "substitutions": changes, "non_bgr555_colors": invalid,
            "suggested_single_slot_merges": sorted(suggestions, key=lambda s: (
                s["squared_rgb_distance"], s["component"], s["from"]))}


def image_colors(image: Image.Image) -> list[str]:
    pixels = list(image.convert("RGBA").get_flattened_data())
    if any(p[3] not in (0, 255) for p in pixels):
        raise ValueError("native palettes require binary alpha")
    return [hex_rgb(c) for c in sorted({p[:3] for p in pixels if p[3]})]


def verify_native(indexed: Path, palette: Path, native: Path, frame_size: list[int]) -> dict:
    """Check a horizontal frame atlas, frame-major 8x8 tiles, low nibble first.

    This is a verifier, not an exporter: all three representations must already
    exist. Frame dimensions/order are explicit and never inferred from art.
    """
    if (not isinstance(frame_size, list) or len(frame_size) != 2
            or any(type(v) is not int or v <= 0 or v % 8 for v in frame_size)):
        raise ValueError("native frame size needs two positive multiples of eight")
    with Image.open(indexed) as im:
        if (im.format != "PNG" or im.mode != "P" or getattr(im, "n_frames", 1) != 1
                or im.info.get("transparency") != 0 or len(im.getpalette()) != 48):
            raise ValueError("native PNG needs 16 RGB palette entries and transparent index zero")
        image = im.copy()
    w, h = frame_size
    if image.height != h or image.width % w or max(image.get_flattened_data()) > 15:
        raise ValueError("indexed sheet does not match horizontal 4bpp frame geometry")
    colors = image.getpalette()
    expected_palette = struct.pack("<16H", *(bgr555(colors[i:i+3]) for i in range(0, 48, 3)))
    expected_native = bytearray()
    decoded = Image.new("P", image.size)
    raw = native.read_bytes()
    for f in range(image.width // w):
        for ty in range(0, h, 8):
            for tx in range(0, w, 8):
                for y in range(8):
                    for x in range(0, 8, 2):
                        px, py = f*w+tx+x, ty+y
                        expected_native.append(image.getpixel((px, py)) | image.getpixel((px+1, py)) << 4)
                        k = len(expected_native)-1
                        if k < len(raw):
                            decoded.putpixel((px, py), raw[k] & 15)
                            decoded.putpixel((px+1, py), raw[k] >> 4)
    checks = {"palette_bytes_exact": expected_palette == palette.read_bytes(),
              "native_bytes_exact": bytes(expected_native) == raw,
              "decoded_indices_exact": len(raw) == len(expected_native) and decoded.tobytes() == image.tobytes()}
    return {"ok": all(checks.values()), "approved": False, **checks,
            "stored_frames": image.width // w, "frame_size": frame_size}
