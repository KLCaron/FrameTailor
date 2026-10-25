# SPDX-License-Identifier: Apache-2.0
"""Source-authored ownership and appearance-specific native-pixel checks.

Zero means unresolved, never protected. Labels name components, not colors.
Ordered binary layers declare final foreground ownership without painting art.
"""
import json
from pathlib import Path

from PIL import Image

from .native import structure_digest
from .palette import image_colors, plan_palette, rgb
from .pipeline import digest, load, local_file, structure

KINDS = {"identity", "clothing", "accessory", "costume", "equipment", "exterior"}
PIXEL_CHECKS = {"protected_occlusion", "protected_rgba", "source_survivor", "material_rgb", "pose_consistency"}


def _config(path):
    value = json.loads(path.read_text())
    if not isinstance(value, dict) or type(value.get("schema_version")) is not int or value["schema_version"] != 1:
        raise ValueError("expected ownership schema_version 1")
    return value


def label_image(path, size, binary=False):
    with Image.open(path) as im:
        if (im.format != "PNG" or im.mode != "L" or getattr(im, "n_frames", 1) != 1
                or im.size != size):
            raise ValueError("ownership/material maps must be static L-mode PNGs matching the sheet")
        result = im.copy()
    if binary and not set(result.get_flattened_data()) <= {0, 255}:
        raise ValueError("ownership layer masks must be binary (0/255)")
    return result


def _components(value):
    if not isinstance(value, dict) or not value:
        raise ValueError("define named ownership components")
    result = {}
    for label, component in value.items():
        if (not isinstance(label, str) or not label.isdecimal() or str(int(label)) != label
                or not 1 <= int(label) <= 255 or not isinstance(component, dict)
                or component.get("kind") not in KINDS or not isinstance(component.get("name"), str)
                or not component["name"].strip()):
            raise ValueError("components need canonical ids 1..255, names and known kinds")
        result[int(label)] = component
    if len({v["name"] for v in result.values()}) != len(result):
        raise ValueError("component names must be unique")
    return result


def _point(point, entry):
    return (isinstance(point, list) and len(point) == 2 and all(type(v) is int for v in point)
            and 0 <= point[0] < entry["rect"][2] and 0 <= point[1] < entry["rect"][3])


def _topology(frame):
    """Review hints only: four-connected enclosed air, eight-connected specks."""
    w, h = frame.size
    opaque = {(x, y) for y in range(h) for x in range(w) if frame.getpixel((x, y))[3]}
    air = {(x, y) for y in range(h) for x in range(w)} - opaque
    edge = {p for p in air if p[0] in (0, w-1) or p[1] in (0, h-1)}
    outside, todo = set(edge), list(edge)
    while todo:
        x, y = todo.pop()
        for p in ((x-1, y), (x+1, y), (x, y-1), (x, y+1)):
            if p in air and p not in outside:
                outside.add(p)
                todo.append(p)
    specks = {p for p in opaque if not any((p[0]+dx, p[1]+dy) in opaque
              for dx in (-1, 0, 1) for dy in (-1, 0, 1) if (dx, dy) != (0, 0))}
    return air - outside, specks


def inspect_ownership(source: Path, candidate: Path, template_path: Path, appearance_path: Path):
    """Return diagnostics and resolved ownership maps. Never modify input art.

    The source template can extend an existing native template and reuse its
    poses/anchors verbatim. An appearance supplies policy, ordered layers and
    a material map. Every exception binds one frame-local pixel, check, exact
    expected RGBA and reason; stale exceptions fail rather than hiding drift.
    """
    spec, before, source_frames = load(source)
    target, after, target_frames = load(candidate)
    template, appearance = _config(template_path), _config(appearance_path)
    if (template.get("source_sheet_sha256") != digest(local_file(source.parent, spec["sheet"]))
            or template.get("source_structure_sha256") != structure_digest(spec)):
        raise ValueError("ownership template does not match source sheet/animation structure")
    if structure(spec) != structure(target) or before.size != after.size:
        raise ValueError("ownership review requires unchanged animation metadata and sheet dimensions")
    components = _components(template.get("components"))
    extra = appearance.get("components", {})
    if extra:
        added = _components(extra)
        if set(components) & set(added) or any(c["kind"] != "costume" for c in added.values()):
            raise ValueError("appearance may only add new costume component ids")
        components.update(added)
        if len({v["name"] for v in components.values()}) != len(components):
            raise ValueError("component names must be unique")
    source_map = label_image(local_file(template_path.parent, template.get("ownership")), before.size)
    if not set(source_map.get_flattened_data()) <= {0, *map(int, template["components"])}:
        raise ValueError("undefined source ownership label")
    final_map = source_map.copy()
    layers = appearance.get("layers")
    if not isinstance(layers, list):
        raise ValueError("appearance needs an explicit back-to-front layers list")
    names = set()
    for layer in layers:
        if (not isinstance(layer, dict) or not isinstance(layer.get("name"), str) or not layer["name"]
                or layer["name"] in names or type(layer.get("owner")) is not int
                or layer["owner"] not in components):
            raise ValueError("layer needs a unique name and known integer owner")
        names.add(layer["name"])
        mask = label_image(local_file(appearance_path.parent, layer.get("mask")), before.size, binary=True)
        final_map.paste(layer["owner"], (0, 0), mask)
    policy = appearance.get("source_policy")
    required = {str(k) for k, c in components.items() if c["kind"] in {"clothing", "accessory"}}
    if (not isinstance(policy, dict) or set(policy) != required
            or any(v not in ("retain", "replace", "remove") for v in policy.values())):
        raise ValueError("declare retain/replace/remove for every source clothing/accessory component")
    material_map = label_image(local_file(appearance_path.parent, appearance.get("material_map")), before.size)
    materials, labels = appearance.get("materials"), appearance.get("material_labels")
    if not isinstance(materials, dict) or not isinstance(labels, dict):
        raise ValueError("appearance needs canonical RGB materials and material_labels")
    if not all(isinstance(n, str) and n for n in materials):
        raise ValueError("material names must be nonempty strings")
    canonical = {name: (*rgb(value), 255) for name, value in materials.items()}
    material_colors = {}
    for k, role in labels.items():
        if (not isinstance(k, str) or not k.isdecimal() or str(int(k)) != k
                or not 1 <= int(k) <= 255 or not isinstance(role, str) or role not in canonical):
            raise ValueError("material labels need canonical ids 1..255 and known roles")
        material_colors[int(k)] = canonical[role]
    if not set(material_map.get_flattened_data()) <= {0, *material_colors}:
        raise ValueError("undefined material label")
    entries = {e["id"]: e for e in spec["frames"]}
    exceptions = appearance.get("exceptions", [])
    if not isinstance(exceptions, list):
        raise ValueError("exceptions must be a list")
    exception_map = {}
    for ex in exceptions:
        if (not isinstance(ex, dict) or ex.get("frame") not in entries
                or not _point(ex.get("point"), entries[ex["frame"]]) or ex.get("check") not in PIXEL_CHECKS
                or not isinstance(ex.get("reason"), str) or not ex["reason"].strip()
                or not isinstance(ex.get("rgba"), list) or len(ex["rgba"]) != 4
                or any(type(v) is not int or not 0 <= v <= 255 for v in ex["rgba"])):
            raise ValueError("exception needs frame, point, known check, exact RGBA and reason")
        key = (ex["frame"], *ex["point"], ex["check"])
        if key in exception_map:
            raise ValueError("duplicate pixel exception")
        exception_map[key] = ex
    diagnostics, applied, used = [], [], set()

    def issue(check, entry, px, py, expected=None, severity="error"):
        x, y = entry["rect"][:2]
        rgba = list(after.getpixel((x+px, y+py)))
        key = (entry["id"], px, py, check)
        record = {"check": check, "severity": severity, "frame": entry["id"], "point": [px, py],
                  "sheet_point": [x+px, y+py], "source_rgba": list(before.getpixel((x+px, y+py))),
                  "actual_rgba": rgba, "source_owner": source_map.getpixel((x+px, y+py)),
                  "expected_owner": final_map.getpixel((x+px, y+py)), "expected": expected}
        ex = exception_map.get(key)
        if ex is not None and ex["rgba"] == rgba:
            used.add(key)
            applied.append({**record, "reason": ex["reason"]})
        else:
            diagnostics.append(record)

    occupied = set()
    for entry, a, b in zip(spec["frames"], source_frames, target_frames):
        x, y, w, h = entry["rect"]
        old_holes, old_specks = _topology(a)
        holes, specks = _topology(b)
        for px, py in sorted(holes-old_holes):
            issue("new_enclosed_air", entry, px, py, severity="review")
        for px, py in sorted(specks-old_specks):
            issue("new_isolated_pixel", entry, px, py, severity="review")
        for py in range(h):
            for px in range(w):
                p = (x+px, y+py)
                occupied.add(p)
                old, new = source_map.getpixel(p), final_map.getpixel(p)
                m = material_map.getpixel(p)
                ca, cb = before.getpixel(p), after.getpixel(p)
                if not old or not new:
                    issue("unresolved_ownership", entry, px, py)
                    continue
                oc, nc = components[old], components[new]
                if (oc["kind"] == "exterior") != (ca[3] == 0):
                    issue("source_ownership_alpha", entry, px, py)
                if nc["kind"] == "exterior":
                    if cb[3]:
                        issue("unexpected_silhouette", entry, px, py, "transparent exterior")
                elif cb[3] != 255:
                    issue("missing_component_pixel", entry, px, py, "opaque component")
                action = policy.get(str(old), "retain")
                if oc["kind"] in {"identity", "equipment"} or action == "retain" and oc["kind"] != "exterior":
                    if new != old:
                        issue("protected_occlusion", entry, px, py, oc["name"])
                    elif cb != ca:
                        issue("protected_rgba", entry, px, py, list(ca))
                elif action in {"remove", "replace"}:
                    if new == old:
                        issue("unresolved_replacement", entry, px, py, action)
                    if cb[3] and cb == ca:
                        issue("source_survivor", entry, px, py, oc["name"])
                if nc["kind"] == "costume" and not m:
                    issue("unresolved_material", entry, px, py)
                if m and (nc["kind"] == "exterior" or cb != material_colors[m]):
                    issue("material_rgb", entry, px, py, list(material_colors[m]))
    for y in range(before.height):
        for x in range(before.width):
            if (x, y) not in occupied and (before.getpixel((x, y)) != after.getpixel((x, y))
                    or source_map.getpixel((x, y)) or final_map.getpixel((x, y)) or material_map.getpixel((x, y))):
                raise ValueError("art or ownership labels outside declared frames")
    # Explicit related-pose samples avoid assuming identical garment silhouettes.
    relations = appearance.get("relations", [])
    if not isinstance(relations, list):
        raise ValueError("relations must be a list")
    for relation in relations:
        samples = relation.get("samples") if isinstance(relation, dict) else None
        if not isinstance(samples, list) or len(samples) < 2:
            raise ValueError("pose relation needs at least two frame/point samples")
        values = []
        for sample in samples:
            if (not isinstance(sample, dict) or sample.get("frame") not in entries
                    or not _point(sample.get("point"), entries[sample["frame"]])):
                raise ValueError("pose relation sample needs an in-frame point")
            entry = entries[sample["frame"]]
            px, py = sample["point"]
            p = (entry["rect"][0]+px, entry["rect"][1]+py)
            value = (final_map.getpixel(p), material_map.getpixel(p), after.getpixel(p))
            if values and value != values[0]:
                issue("pose_consistency", entry, px, py, list(values[0]))
            values.append(value)
    for key, ex in exception_map.items():
        if key not in used:
            diagnostics.append({**ex, "exception_check": ex["check"], "check": "stale_exception", "severity": "error"})
    palette_components = appearance.get("palette_components", {})
    if not isinstance(palette_components, dict) or "candidate" in palette_components:
        raise ValueError("palette_components must map unique component names to relative image paths")
    palette_colors = {"candidate": image_colors(after)}
    for name, path in palette_components.items():
        with Image.open(local_file(appearance_path.parent, path)) as im:
            palette_colors[name] = image_colors(im)
    palette = plan_palette({k: v for k, v in palette_colors.items() if v}) if any(palette_colors.values()) else None
    counts = {k: sum(d["check"] == k for d in diagnostics) for k in sorted({d["check"] for d in diagnostics})}
    assets = {"source_manifest": source, "candidate_manifest": candidate,
              "source_sheet": local_file(source.parent, spec["sheet"]),
              "candidate_sheet": local_file(candidate.parent, target["sheet"]),
              "template": template_path, "appearance": appearance_path,
              "source_ownership": local_file(template_path.parent, template["ownership"]),
              "material_map": local_file(appearance_path.parent, appearance["material_map"])}
    assets.update({f"layer_{i}": local_file(appearance_path.parent, layer["mask"]) for i, layer in enumerate(layers)})
    assets.update({f"palette_component_{name}": local_file(appearance_path.parent, path)
                   for name, path in palette_components.items()})
    report = {"ok": not any(d["severity"] == "error" for d in diagnostics) and (palette is None or palette["ok"]),
              "approved": False, "profile": "semantic-ownership-v1", "diagnostics": diagnostics,
              "counts": counts, "applied_exceptions": applied, "palette": palette,
              "canonical_materials": materials,
              "components": {str(k): v for k, v in components.items()}, "source_policy": policy,
              "review_required": ["ownership labels", "silhouette and outlines", "costume readability", "motion and depth"],
              "implementation_sha256": {p.name: digest(p) for p in sorted(Path(__file__).parent.glob("*.py"))},
              "inputs_sha256": {k: digest(p) for k, p in assets.items()}}
    return report, source_map, final_map, material_map


def validate_ownership(source: Path, candidate: Path, template: Path, appearance: Path) -> dict:
    return inspect_ownership(source, candidate, template, appearance)[0]
