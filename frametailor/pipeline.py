# SPDX-License-Identifier: Apache-2.0
"""Small, offline pipeline. Source manifests are authoritative; never infer poses."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import platform
import re
import tempfile
import zlib
from typing import Callable

from PIL import Image, ImageDraw, __version__ as pillow_version

from . import __version__

Transform = Callable[[Image.Image], Image.Image]


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def integer(value: object, minimum: int = 0) -> bool:
    return type(value) is int and value >= minimum


def local_file(root: Path, value: object) -> Path:
    if not isinstance(value, str) or not value or Path(value).is_absolute():
        raise ValueError("asset paths must be nonempty relative paths")
    path = (root / value).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("asset path escapes manifest directory")
    return path


def load(path: Path) -> tuple[dict, Image.Image, list[Image.Image]]:
    """Read a PNG atlas with explicit frame rectangles, timing and anchors."""
    spec = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(spec, dict) or type(spec.get("schema_version")) is not int or spec.get("schema_version") != 1:
        raise ValueError("expected manifest schema_version 1")
    if not isinstance(spec.get("animation"), str) or not spec["animation"]:
        raise ValueError("animation must be a nonempty name")
    if type(spec.get("loop")) is not bool:
        raise ValueError("loop must be boolean")
    with Image.open(local_file(path.parent, spec.get("sheet"))) as image:
        if image.format != "PNG" or getattr(image, "n_frames", 1) != 1:
            raise ValueError("sheet must be a static PNG")
        sheet = image.convert("RGBA")
    entries = spec.get("frames")
    if not isinstance(entries, list) or not entries:
        raise ValueError("frames must be a nonempty list")
    seen = set()
    frames = []
    rectangles = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("each frame must be an object")
        name = entry.get("id")
        if not isinstance(name, str) or not name or name in seen:
            raise ValueError("frame ids must be nonempty and unique")
        seen.add(name)
        rect = entry.get("rect")
        if (not isinstance(rect, list) or len(rect) != 4
                or not all(integer(v) for v in rect) or not all(v > 0 for v in rect[2:])):
            raise ValueError("rect must be [x, y, positive width, positive height]")
        x, y, w, h = rect
        if x + w > sheet.width or y + h > sheet.height:
            raise ValueError("frame rectangle outside sheet")
        if any(x < rx + rw and rx < x + w and y < ry + rh and ry < y + h
               for rx, ry, rw, rh in rectangles):
            raise ValueError("frame rectangles must not overlap")
        rectangles.append(rect)
        anchor = entry.get("anchor")
        if (not isinstance(anchor, list) or len(anchor) != 2
                or not all(integer(v) for v in anchor) or anchor[0] > w or anchor[1] > h):
            raise ValueError("anchor must be an integer point within the frame edges")
        if not integer(entry.get("duration_ms"), 1):
            raise ValueError("duration_ms must be a positive integer")
        frames.append(sheet.crop((x, y, x + w, y + h)))
    return spec, sheet, frames


def palette_transform(path: Path) -> tuple[Transform, dict]:
    config = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(config, dict) or config.get("type") != "palette" or not isinstance(config.get("colors"), dict) or not config["colors"]:
        raise ValueError("expected type=palette and a nonempty colors mapping")
    def rgb(value: object) -> tuple[int, ...]:
        if not isinstance(value, str) or re.fullmatch(r"#[0-9a-fA-F]{6}", value) is None:
            raise ValueError("palette colors must be #RRGGBB")
        return tuple(bytes.fromhex(value[1:]))
    pairs = {}
    for source, target in config["colors"].items():
        key = rgb(source)
        if key in pairs:
            raise ValueError("duplicate normalized source color")
        pairs[key] = rgb(target)

    def transform(frame: Image.Image) -> Image.Image:
        output = frame.copy()
        # Simultaneous lookup: A -> B and B -> C does not turn A into C.
        output.putdata([(*pairs.get(p[:3], p[:3]), p[3]) if p[3] else p
                        for p in frame.get_flattened_data()])
        return output
    return transform, config


def structure(spec: dict) -> dict:
    result = deepcopy(spec)
    result.pop("sheet", None)
    return result


def validate(source: Path, candidate: Path) -> dict:
    """Strict profile: geometry, timing, order, anchors and every alpha byte."""
    before, source_sheet, source_frames = load(source)
    after, target_sheet, target_frames = load(candidate)
    errors = []
    if structure(before) != structure(after):
        errors.append("animation metadata changed (order, rects, timing, anchors, loop or extra metadata)")
    if source_sheet.size != target_sheet.size:
        errors.append("sheet dimensions changed")
    changed = 0
    for index, (a, b) in enumerate(zip(source_frames, target_frames)):
        if a.size != b.size:
            errors.append(f"frame {index}: dimensions changed")
        elif a.getchannel("A").tobytes() != b.getchannel("A").tobytes():
            errors.append(f"frame {index}: alpha mask changed (alignment/silhouette)")
        else:
            changed += sum(p != q for p, q in zip(a.get_flattened_data(), b.get_flattened_data()))
    if source_sheet.size == target_sheet.size:
        # Pixels outside all declared frames must also survive unchanged.
        a, b = source_sheet.copy(), target_sheet.copy()
        for frame in before["frames"]:
            x, y, w, h = frame["rect"]
            a.paste((0, 0, 0, 0), (x, y, x + w, y + h))
            b.paste((0, 0, 0, 0), (x, y, x + w, y + h))
        if a.tobytes() != b.tobytes():
            errors.append("pixels outside declared frames changed")
    return {"ok": not errors, "profile": "exact-alpha-v1", "errors": errors,
            "frame_count": len(target_frames), "changed_pixels": changed}


def comparison(before: list[Image.Image], after: list[Image.Image], out: Path) -> None:
    cell_w = max(im.width for im in before)
    cell_h = max(im.height for im in before)
    canvas = Image.new("RGB", (cell_w * len(before), cell_h * 2), "#e5e7eb")
    draw = ImageDraw.Draw(canvas)
    for y in range(0, canvas.height, 4):
        for x in range(0, canvas.width, 4):
            if (x // 4 + y // 4) % 2:
                draw.rectangle((x, y, x + 3, y + 3), fill="#b9bec7")
    for row, frames in enumerate((before, after)):
        for col, frame in enumerate(frames):
            canvas.paste(frame, (col * cell_w, row * cell_h), frame)
    canvas.resize((canvas.width * 6, canvas.height * 6), Image.Resampling.NEAREST).save(out)


def preview(frames: list[Image.Image], spec: dict, path: Path) -> None:
    # APNG needs a common canvas; do not silently pad differently sized frames.
    if len({im.size for im in frames}) != 1:
        return
    options = {"loop": 0} if spec["loop"] else {"loop": 1}
    frames[0].save(path, format="PNG", save_all=True, append_images=frames[1:],
                   duration=[f["duration_ms"] for f in spec["frames"]],
                   disposal=0, blend=0, **options)


def run(source: Path, config_path: Path, out: Path) -> dict:
    """Stage a complete validated bundle, then publish into a fresh directory."""
    if out.exists():
        raise ValueError("output directory already exists; choose a fresh path")
    spec, sheet, frames = load(source)
    transform, config = palette_transform(config_path)
    transformed = [transform(frame) for frame in frames]
    if all(a.tobytes() == b.tobytes() for a, b in zip(frames, transformed)):
        raise ValueError("palette made no visible change; check source colors")
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".frametailor-", dir=out.parent) as staging:
        root = Path(staging) / "bundle"
        root.mkdir()
        (root / "frames").mkdir()
        for i, (entry, frame) in enumerate(zip(spec["frames"], transformed)):
            sheet.paste(frame, tuple(entry["rect"][:2]))  # no alpha compositing
            frame.save(root / "frames" / f"{i:04d}.png")
        sheet.save(root / "sheet.png")
        target = deepcopy(spec)
        target["sheet"] = "sheet.png"
        write_json(root / "animation.json", target)
        report = validate(source, root / "animation.json")
        if not report["ok"]:
            raise ValueError("validation failed: " + "; ".join(report["errors"]))
        write_json(root / "validation.json", report)
        comparison(frames, transformed, root / "comparison.png")
        preview(transformed, spec, root / "preview.png")
        # Keep portable copies so source/config hashes remain independently auditable.
        (root / "inputs").mkdir()
        source_sheet = local_file(source.parent, spec["sheet"])
        (root / "inputs" / "sheet.png").write_bytes(source_sheet.read_bytes())
        normalized = deepcopy(spec)
        normalized["sheet"] = "sheet.png"
        write_json(root / "inputs" / "animation.json", normalized)
        (root / "inputs" / "source-manifest.json").write_bytes(source.read_bytes())
        (root / "inputs" / "transform.json").write_bytes(config_path.read_bytes())
        artifacts = {p.relative_to(root).as_posix(): digest(p)
                     for p in sorted(root.rglob("*")) if p.is_file()}
        write_json(root / "provenance.json", {
            "schema_version": 1, "frametailor_version": __version__,
            "python_version": platform.python_version(), "pillow_version": pillow_version,
            "zlib_version": zlib.ZLIB_RUNTIME_VERSION,
            "implementation_sha256": {p.name: digest(p) for p in sorted(Path(__file__).parent.glob("*.py"))},
            "provider": "deterministic-palette-v1", "parameters": config,
            "source_manifest_sha256": digest(source), "source_sheet_sha256": digest(source_sheet),
            "transform_sha256": digest(config_path), "artifacts_sha256": artifacts,
            "reproducibility": "same inputs and runtime versions; no timestamp or absolute paths",
        })
        root.rename(out)
    return report
