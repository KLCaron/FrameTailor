# SPDX-License-Identifier: Apache-2.0
"""Deterministic, binary-mask RGBA replacement; no provider integration."""
from copy import deepcopy
from pathlib import Path
import platform
import tempfile

from PIL import Image, ImageDraw, __version__ as pillow_version

from . import __version__
from .benchmark import load_editable_mask, validate_outfit
from .pipeline import digest, load, local_file, preview, structure, validate, write_json


def masked_replace(source: Image.Image, candidate: Image.Image, mask: Image.Image) -> Image.Image:
    """Copy all four candidate channels at 255; keep source bytes at 0.

    Transparent candidate pixels erase source pixels inside the mask. Partial
    alpha is copied verbatim, never blended, thresholded or made opaque.
    White pixels on transparent source explicitly permit silhouette expansion.
    """
    if source.mode != "RGBA" or candidate.mode != "RGBA" or mask.mode != "L":
        raise ValueError("expected RGBA source/candidate and grayscale mask")
    if source.size != candidate.size or source.size != mask.size:
        raise ValueError("source, candidate and mask dimensions must match")
    if not set(mask.get_flattened_data()) <= {0, 255}:
        raise ValueError("mask must contain only 0/255")
    result = source.copy()
    result.paste(candidate, (0, 0), mask)
    return result


def comparison_three(source: list, candidate: list, result: list, path: Path) -> None:
    """Columns: original, candidate, composite. Rows: source frame order."""
    width = max(im.width for im in source)
    height = max(im.height for im in source)
    scale, header = 4, 24
    canvas = Image.new("RGB", (width * 3 * scale, height * len(source) * scale + header), "#e5e7eb")
    draw = ImageDraw.Draw(canvas)
    for y in range(header, canvas.height, 16):
        for x in range(0, canvas.width, 16):
            if (x // 16 + (y - header) // 16) % 2:
                draw.rectangle((x, y, x + 15, y + 15), fill="#b9bec7")
    for col, (label, frames) in enumerate(zip(("Original", "Raw candidate", "Composite"), (source, candidate, result))):
        draw.text((col * width * scale + 6, 5), label, fill="black")
        for row, frame in enumerate(frames):
            enlarged = frame.resize((frame.width * scale, frame.height * scale), Image.Resampling.NEAREST)
            canvas.paste(enlarged, (col * width * scale, row * height * scale + header), enlarged)
    canvas.save(path)


def composite(source: Path, candidate: Path, editable_mask: Path, out: Path) -> dict:
    """Write an auditable review bundle; structural success never grants approval."""
    if out.exists():
        raise ValueError("output directory already exists; choose a fresh path")
    spec, original, before = load(source)
    candidate_spec, raw, raw_frames = load(candidate)
    if structure(spec) != structure(candidate_spec) or original.size != raw.size:
        raise ValueError("candidate metadata and sheet dimensions must match source")
    mask = load_editable_mask(spec, original, editable_mask)
    result = masked_replace(original, raw, mask)
    transferred = Image.new("L", original.size)
    discarded = Image.new("L", original.size)
    expansion_discarded = Image.new("L", original.size)
    for i, (a, b, allowed) in enumerate(zip(original.get_flattened_data(), raw.get_flattened_data(), mask.get_flattened_data())):
        point = (i % original.width, i // original.width)
        if a != b:
            (transferred if allowed else discarded).putpixel(point, 255)
            if not allowed and a[3] == 0 and b[3] > 0:
                expansion_discarded.putpixel(point, 255)
    frames, records = [], []
    for entry in spec["frames"]:
        x, y, w, h = entry["rect"]
        box = (x, y, x + w, y + h)
        frames.append(result.crop(box))
        a, b = original.crop(box), raw.crop(box)
        permitted = mask.crop(box)
        records.append({"id": entry["id"],
            "authorized_pixels": sum(v == 255 for v in permitted.get_flattened_data()),
            "transferred_changed_pixels": sum(v == 255 for v in transferred.crop(box).get_flattened_data()),
            "discarded_unauthorized_changes": sum(v == 255 for v in discarded.crop(box).get_flattened_data()),
            "discarded_visible_expansion_pixels": sum(v == 255 for v in expansion_discarded.crop(box).get_flattened_data()),
            "authorized_alpha_changes": sum(p[3] != q[3] and bool(m) for p, q, m in zip(a.get_flattened_data(), b.get_flattened_data(), permitted.get_flattened_data()))})
    protected_changes = sum(p != q and not m for p, q, m in zip(original.get_flattened_data(), result.get_flattened_data(), mask.get_flattened_data()))
    report = {"ok": protected_changes == 0, "operation": "binary-rgba-replace-v1",
        "approved": False, "review_status": "pending", "remaining_unauthorized_changes": protected_changes,
        "transparency": "copy candidate RGBA verbatim inside mask; retain source RGBA outside; no blending",
        "clipping_review": "required: discarded expansion map flags possible detail loss, not semantic clothing detection",
        "frames": records}
    for key in ("authorized_pixels", "transferred_changed_pixels", "discarded_unauthorized_changes", "discarded_visible_expansion_pixels", "authorized_alpha_changes"):
        report[key] = sum(frame[key] for frame in records)
    report["discarded_unauthorized_changes"] = sum(v == 255 for v in discarded.get_flattened_data())
    report["discarded_visible_expansion_pixels"] = sum(v == 255 for v in expansion_discarded.get_flattened_data())
    report["discarded_padding_changes"] = report["discarded_unauthorized_changes"] - sum(frame["discarded_unauthorized_changes"] for frame in records)
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".frametailor-", dir=out.parent) as staging:
        root = Path(staging) / "bundle"
        root.mkdir()
        (root / "frames").mkdir()
        (root / "inputs").mkdir()
        for i, frame in enumerate(frames):
            frame.save(root / "frames" / f"{i:04d}.png")
        result.save(root / "sheet.png")
        output_spec = deepcopy(spec)
        output_spec["sheet"] = "sheet.png"
        write_json(root / "animation.json", output_spec)
        for path, name in ((source, "source-manifest.json"), (candidate, "candidate-manifest.json"),
                           (local_file(source.parent, spec["sheet"]), "source.png"),
                           (local_file(candidate.parent, candidate_spec["sheet"]), "candidate.png"),
                           (editable_mask, "editable-mask.png")):
            (root / "inputs" / name).write_bytes(path.read_bytes())
        transferred.save(root / "transferred-changes.png")
        discarded.save(root / "discarded-changes.png")
        expansion_discarded.save(root / "discarded-expansion.png")
        checks = {"raw_outfit": validate_outfit(source, candidate, editable_mask),
                  "raw_exact_alpha": validate(source, candidate),
                  "composite_outfit": validate_outfit(source, root / "animation.json", editable_mask),
                  "composite_exact_alpha": validate(source, root / "animation.json")}
        report["ok"] = report["ok"] and checks["composite_outfit"]["ok"]
        report["review_status"] = "pending" if report["ok"] else "blocked"
        write_json(root / "validation.json", checks)
        write_json(root / "compositing.json", report)
        comparison_three(before, raw_frames, frames, root / "comparison.png")
        preview(frames, spec, root / "preview.png")
        write_json(root / "provenance.json", {"frametailor_version": __version__,
            "python_version": platform.python_version(), "pillow_version": pillow_version,
            "implementation_sha256": {p.name: digest(p) for p in sorted(Path(__file__).parent.glob("*.py"))},
            "inputs_sha256": {p.name: digest(p) for p in sorted((root / "inputs").iterdir())},
            "artifacts_sha256": {p.relative_to(root).as_posix(): digest(p) for p in sorted(root.rglob("*")) if p.is_file()}})
        root.rename(out)
    return report
