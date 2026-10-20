# SPDX-License-Identifier: Apache-2.0
"""Source-bound semantic labels and anchored, integer-pixel garment details.

This pilot retains source silhouettes. Region zero protects all source channels;
other labels name material roles and shades, independent of source RGB values.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import tempfile

from PIL import Image

from .composite import composite
from .pipeline import digest, integer, load, local_file, structure, write_json


def structure_digest(spec: dict) -> str:
    return hashlib.sha256(json.dumps(structure(spec), sort_keys=True).encode()).hexdigest()


def _point(value: object) -> bool:
    return (isinstance(value, list) and len(value) == 2
            and all(type(v) is int for v in value))


def render(source: Path, template_path: Path, outfit_path: Path) -> tuple[Image.Image, Image.Image, dict]:
    """Render without resampling, blending, inferred regions or silent clipping."""
    spec, sheet, frames = load(source)
    template = json.loads(template_path.read_text())
    outfit = json.loads(outfit_path.read_text())
    for config in (template, outfit):
        if not isinstance(config, dict) or type(config.get("schema_version")) is not int or config["schema_version"] != 1:
            raise ValueError("expected native schema_version 1")
    if (template.get("source_sheet_sha256") != digest(local_file(source.parent, spec["sheet"]))
            or template.get("source_structure_sha256") != structure_digest(spec)):
        raise ValueError("template does not match source sheet/animation structure")
    with Image.open(local_file(template_path.parent, template.get("regions"))) as image:
        if image.format != "PNG" or image.mode != "L" or getattr(image, "n_frames", 1) != 1:
            raise ValueError("regions must be a static L-mode PNG")
        regions = image.copy()
    if regions.size != sheet.size:
        raise ValueError("region dimensions must match source")
    labels = template.get("labels")
    poses = template.get("poses")
    if not isinstance(labels, dict) or not labels or not all(
            k.isdecimal() and str(int(k)) == k and 1 <= int(k) <= 255 for k in labels):
        raise ValueError("labels must define canonical region ids 1..255")
    if not isinstance(poses, dict) or set(poses) != {f["id"] for f in spec["frames"]}:
        raise ValueError("poses must cover exactly the source frame ids")
    if not set(regions.get_flattened_data()) <= {0, *map(int, labels)}:
        raise ValueError("undefined region label")
    materials = outfit.get("materials")
    views = outfit.get("views")
    if not isinstance(materials, dict) or not isinstance(views, dict):
        raise ValueError("outfit requires materials and view-specific details")

    def color(role: str) -> tuple:
        if not isinstance(role, str) or role.count(".") != 1:
            raise ValueError("color role must be material.shade")
        material, shade = role.split(".")
        shades = materials.get(material)
        value = shades.get(shade) if isinstance(shades, dict) else None
        if not isinstance(value, str) or re.fullmatch(r"#[0-9a-fA-F]{6}", value) is None:
            raise ValueError(f"undefined/invalid material color: {role}")
        return (*bytes.fromhex(value[1:]), 255)

    base_colors = {int(k): color(role) for k, role in labels.items()}
    occupied = Image.new("L", sheet.size)
    mask = regions.point(lambda p: 255 if p else 0)
    result = sheet.copy()
    records = []
    for entry, frame in zip(spec["frames"], frames):
        x, y, w, h = entry["rect"]
        occupied.paste(255, (x, y, x + w, y + h))
        pose = poses[entry["id"]]
        if not isinstance(pose, dict) or not isinstance(pose.get("view"), str):
            raise ValueError("pose needs an explicit view")
        anchors, exceptions = pose.get("anchors"), pose.get("exceptions")
        if (not isinstance(anchors, dict) or not all(_point(p) and 0 <= p[0] < w and 0 <= p[1] < h
                                                   for p in anchors.values())
                or not isinstance(exceptions, list) or not all(isinstance(s, str) and s.strip() for s in exceptions)):
            raise ValueError("pose requires in-frame integer anchors and an explicit exceptions list")
        details = views.get(pose.get("view"))
        if not isinstance(details, list) or not details:
            raise ValueError("every pose view needs explicit garment details")
        for py in range(h):
            for px in range(w):
                label = regions.getpixel((x + px, y + py))
                if label:
                    if frame.getpixel((px, py))[3] == 0:
                        raise ValueError("native pilot regions cannot include transparent source pixels")
                    result.putpixel((x + px, y + py), (*base_colors[label][:3], frame.getpixel((px, py))[3]))
        base = result.crop((x, y, x + w, y + h))
        placements, used, names = [], set(), set()
        for detail in details:
            if not isinstance(detail, dict):
                raise ValueError("detail must be an object")
            name, anchor = detail.get("name"), detail.get("anchor")
            allowed, pixels = detail.get("regions"), detail.get("pixels")
            if (not isinstance(name, str) or not name or name in names or not isinstance(anchor, str) or anchor not in anchors
                    or not isinstance(allowed, list) or not allowed
                    or not all(type(v) is int and str(v) in labels for v in allowed)
                    or not isinstance(pixels, list) or not pixels):
                raise ValueError("detail needs a unique name, known anchor/regions and pixels")
            names.add(name)
            ax, ay = anchors[anchor]
            points = []
            changed = 0
            for pixel in pixels:
                if not isinstance(pixel, list) or len(pixel) != 3 or not _point(pixel[:2]):
                    raise ValueError("detail pixel must be [integer dx, integer dy, material.shade]")
                dx, dy, role = pixel
                px, py = ax + dx, ay + dy
                if (not (0 <= px < w and 0 <= py < h)
                        or regions.getpixel((x + px, y + py)) not in allowed):
                    raise ValueError(f"{entry['id']}/{name}: detail outside declared garment region at {[px, py]}")
                if (px, py) in used:
                    raise ValueError("overlapping detail pixels; author the final pattern explicitly")
                used.add((px, py))
                rgba = (*color(role)[:3], frame.getpixel((px, py))[3])
                changed += rgba != base.getpixel((px, py))
                result.putpixel((x + px, y + py), rgba)
                points.append([px, py])
            if not changed:
                raise ValueError(f"{entry['id']}/{name}: detail has no contrast against base material")
            placements.append({"name": name, "pixels": points, "changed_from_material": changed})
        output = result.crop((x, y, x + w, y + h))
        mappings = {}
        for a, b in zip(frame.get_flattened_data(), output.get_flattened_data()):
            if a[3]:
                mappings.setdefault(a, set()).add(b)
        splits = sum(len(values) > 1 for values in mappings.values())
        if not splits:
            raise ValueError(f"{entry['id']}: output is explainable by palette recoloring alone")
        records.append({"id": entry["id"], "view": pose["view"], "anchors": anchors,
                        "exceptions": exceptions, "details": placements, "source_color_splits": splits})
    if any(m and not o for m, o in zip(mask.get_flattened_data(), occupied.get_flattened_data())):
        raise ValueError("regions outside declared frames")
    limit = outfit.get("max_visible_colors")
    count = len({p[:3] for p in result.get_flattened_data() if p[3]})
    if limit is not None and (not integer(limit, 1) or count > limit):
        raise ValueError(f"visible palette budget exceeded: {count} colors")
    return result, mask, {"operation": "native-regions-v1", "approved": False,
        "visible_colors": count, "max_visible_colors": limit, "frames": records,
        "frame_exception_count": sum(bool(r["exceptions"]) for r in records),
        "detail_pixel_count": sum(len(d["pixels"]) for r in records for d in r["details"]),
        "policy": "source silhouette retained for this pilot; no clipping, fallback or resampling",
        "review_required": ["garment readability", "directional consistency", "motion and occlusion"]}


def native_outfit(source: Path, template: Path, outfit: Path, out: Path) -> dict:
    """Reuse the compositor's validators and review bundle, publishing atomically."""
    if out.exists():
        raise ValueError("output directory already exists; choose a fresh path")
    result, mask, report = render(source, template, outfit)
    spec, _, _ = load(source)
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".frametailor-native-", dir=out.parent) as staging:
        work = Path(staging)
        result.save(work / "candidate.png")
        mask.save(work / "editable-mask.png")
        candidate = deepcopy(spec)
        candidate["sheet"] = "candidate.png"
        write_json(work / "candidate.json", candidate)
        bundle = work / "bundle"
        checks = composite(source, work / "candidate.json", work / "editable-mask.png", bundle,
                           mode="preserve-source-silhouette")
        if not checks["ok"]:
            raise ValueError("native outfit failed structural validation")
        report["ok"] = True
        write_json(bundle / "native.json", report)
        config = json.loads(template.read_text())
        (bundle / "inputs" / "regions.png").write_bytes(local_file(template.parent, config["regions"]).read_bytes())
        config["regions"] = "regions.png"
        write_json(bundle / "inputs" / "template.json", config)
        (bundle / "inputs" / "original-template.json").write_bytes(template.read_bytes())
        (bundle / "inputs" / "outfit.json").write_bytes(outfit.read_bytes())
        portable_source = deepcopy(spec)
        portable_source["sheet"] = "source.png"
        write_json(bundle / "inputs" / "animation.json", portable_source)
        provenance = json.loads((bundle / "provenance.json").read_text())
        provenance["inputs_sha256"] = {p.name: digest(p) for p in sorted((bundle / "inputs").iterdir())}
        provenance["artifacts_sha256"] = {p.relative_to(bundle).as_posix(): digest(p)
            for p in sorted(bundle.rglob("*")) if p.is_file() and p.name != "provenance.json"}
        write_json(bundle / "provenance.json", provenance)
        bundle.rename(out)
    return report
