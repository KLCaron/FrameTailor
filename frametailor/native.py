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


def regions_from_colors(sheet: Image.Image, colors: dict[str, int]) -> Image.Image:
    """Explicit source-palette annotation, not semantic inference.

    Only use where each chosen RGB belongs exclusively to one clothing role in
    every pose. Unmapped and transparent pixels stay protected. Inspect the
    resulting labels before binding a template; shared skin/accessory colors
    need a separately authored region sheet instead.
    """
    if not isinstance(colors, dict) or not colors:
        raise ValueError("provide explicit source RGB-to-region assignments")
    lookup = {}
    for color, label in colors.items():
        if (not isinstance(color, str) or re.fullmatch(r"#[0-9a-fA-F]{6}", color) is None
                or type(label) is not int or not 1 <= label <= 255):
            raise ValueError("source colors need #rrggbb keys and region ids 1..255")
        rgb = tuple(bytes.fromhex(color[1:]))
        if rgb in lookup:
            raise ValueError("duplicate source RGB assignment")
        lookup[rgb] = label
    pixels = list(sheet.convert("RGBA").get_flattened_data())
    visible = {p[:3] for p in pixels if p[3]}
    if not lookup.keys() <= visible:
        raise ValueError("assigned source color is absent from visible pixels")
    regions = Image.new("L", sheet.size)
    regions.putdata([lookup.get(p[:3], 0) if p[3] else 0 for p in pixels])
    return regions


def regions_from_rules(sheet: Image.Image, rules: list[dict]) -> Image.Image:
    """Combine explicit RGB ownership with sheet-local rectangles.

    Rectangles are [x, y, width, height]. Unmatched pixels remain protected.
    Conflicting labels fail; rule order never decides ownership. This annotates
    source art only and does not infer anatomy or derive masks from output.
    """
    if not isinstance(rules, list) or not rules:
        raise ValueError("provide nonempty source ownership rules")
    result = Image.new("L", sheet.size)
    for rule in rules:
        if not isinstance(rule, dict):
            raise ValueError("ownership rule must be an object")
        rect = rule.get("rect")
        if (not isinstance(rect, list) or len(rect) != 4
                or not all(integer(v) for v in rect) or not all(v > 0 for v in rect[2:])):
            raise ValueError("ownership rect must be [x, y, positive width, positive height]")
        x, y, w, h = rect
        if x + w > sheet.width or y + h > sheet.height:
            raise ValueError("ownership rectangle outside source")
        labels = regions_from_colors(sheet.crop((x, y, x + w, y + h)), rule.get("colors"))
        for py in range(h):
            for px in range(w):
                label = labels.getpixel((px, py))
                previous = result.getpixel((x + px, y + py))
                if label and previous not in (0, label):
                    raise ValueError("conflicting source ownership rules")
                if label:
                    result.putpixel((x + px, y + py), label)
    return result


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
    occluder_names = {name for pose in poses.values() if isinstance(pose, dict)
                      and isinstance(pose.get("occluders", {}), dict)
                      for name in pose.get("occluders", {})}
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
        occluders = pose.get("occluders", {})
        if not isinstance(occluders, dict):
            raise ValueError("pose occluders must be named point lists")
        for name, points in occluders.items():
            if (not isinstance(name, str) or not name or not isinstance(points, list) or not points
                    or not all(_point(p) for p in points)
                    or len({tuple(p) for p in points}) != len(points)):
                raise ValueError("occluder needs a name and unique integer points")
            for px, py in points:
                if (not (0 <= px < w and 0 <= py < h)
                        or regions.getpixel((x + px, y + py)) != 0
                        or not frame.getpixel((px, py))[3]):
                    raise ValueError("occluder must name visible protected source pixels")
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
            blockers = detail.get("occluded_by", [])
            if (not isinstance(blockers, list) or not all(isinstance(n, str) and n for n in blockers)
                    or len(set(blockers)) != len(blockers)):
                raise ValueError("occluded_by must list unique occluder names")
            if not set(blockers) <= occluder_names:
                raise ValueError("unknown source occluder")
            # A named part can be absent from a pose; only authored coordinates
            # can occlude. All other out-of-region pixels still fail below.
            blocked = {tuple(p) for n in blockers for p in occluders.get(n, [])}
            ax, ay = anchors[anchor]
            points, hidden = [], []
            changed = 0
            for pixel in pixels:
                if not isinstance(pixel, list) or len(pixel) != 3 or not _point(pixel[:2]):
                    raise ValueError("detail pixel must be [integer dx, integer dy, material.shade]")
                dx, dy, role = pixel
                px, py = ax + dx, ay + dy
                rgba_color = color(role)
                if (px, py) in used:
                    raise ValueError("overlapping detail pixels; author the final pattern explicitly")
                used.add((px, py))
                if (px, py) in blocked:
                    hidden.append([px, py])
                    continue
                if (not (0 <= px < w and 0 <= py < h)
                        or regions.getpixel((x + px, y + py)) not in allowed):
                    raise ValueError(f"{entry['id']}/{name}: detail outside declared garment region at {[px, py]}")
                rgba = (*rgba_color[:3], frame.getpixel((px, py))[3])
                changed += rgba != base.getpixel((px, py))
                result.putpixel((x + px, y + py), rgba)
                points.append([px, py])
            if not changed and (points or not hidden):
                raise ValueError(f"{entry['id']}/{name}: detail has no contrast against base material")
            placements.append({"name": name, "pixels": points, "occluded_pixels": hidden,
                               "changed_from_material": changed})
        output = result.crop((x, y, x + w, y + h))
        mappings = {}
        for a, b in zip(frame.get_flattened_data(), output.get_flattened_data()):
            if a[3]:
                mappings.setdefault(a, set()).add(b)
        splits = sum(len(values) > 1 for values in mappings.values())
        if not splits:
            raise ValueError(f"{entry['id']}: output is explainable by palette recoloring alone")
        records.append({"id": entry["id"], "view": pose["view"], "anchors": anchors,
                        "exceptions": exceptions, "occluders": occluders,
                        "details": placements, "source_color_splits": splits})
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
        "occluded_pixel_count": sum(len(d["occluded_pixels"]) for r in records for d in r["details"]),
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
