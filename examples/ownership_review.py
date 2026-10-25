# SPDX-License-Identifier: Apache-2.0
"""Original tiny robot fixture: shared source facts, removable/retained satchel."""
import argparse
from copy import deepcopy
from pathlib import Path

from PIL import Image

from frametailor.native import structure_digest
from frametailor.palette import hex_rgb
from frametailor.pipeline import digest, write_json
from frametailor.review import review_bundle


def create_fixture(out: Path):
    if out.exists():
        raise ValueError("choose a fresh fixture directory")
    out.mkdir(parents=True)
    source, owners = Image.new("RGBA", (16, 8)), Image.new("L", (16, 8), 1)
    entries, poses = [], {}
    for i in range(2):
        dx = i*8
        def point(x, y, owner, color):
            source.putpixel((dx+x, y), color)
            owners.putpixel((dx+x, y), owner)
        for y in (1, 2):
            for x in (2, 3, 4):
                point(x, y, 2, (123, 90, 82, 255))
        point(2, 1, 2, (255, 255, 255, 255))  # Asymmetric eye tests mirroring.
        for y in (3, 4):
            for x in (2, 3, 4):
                point(x, y, 3, (65, 98, 32, 255))
            # Satchel and head share exactly the same RGB.
            point(5, y, 4, (123, 90, 82, 255))
        point(1, 4+i, 2, (222, 164, 148, 255))
        for y in range(1, 7):
            point(6, y, 5, (131, 131, 139, 255))
        entries.append({"id": f"step-{i}", "rect": [dx, 0, 8, 8], "duration_ms": 100, "anchor": [3, 7]})
        poses[f"step-{i}"] = {"view": "side", "anchors": {"torso": [2, 3], "hand": [1, 4+i]}, "exceptions": []}
    spec = {"schema_version": 1, "animation": "original-robot", "sheet": "source.png", "loop": True, "frames": entries}
    source.save(out / "source.png")
    owners.save(out / "ownership.png")
    write_json(out / "source.json", spec)
    write_json(out / "template.json", {"schema_version": 1, "source_sheet_sha256": digest(out / "source.png"),
        "source_structure_sha256": structure_digest(spec), "ownership": "ownership.png", "poses": poses,
        "components": {"1": {"name": "exterior", "kind": "exterior"}, "2": {"name": "head-and-hand", "kind": "identity"},
                       "3": {"name": "source-shirt", "kind": "clothing"}, "4": {"name": "satchel", "kind": "accessory"},
                       "5": {"name": "held-tool", "kind": "equipment"}}})
    for name, retain, bottom, color in (("coat", False, 5, (41, 90, 131, 255)),
                                       ("vest", True, 4, (139, 82, 148, 255))):
        result = source.copy()
        erase, costume, materials = (Image.new("L", source.size) for _ in range(3))
        for i in range(2):
            for y in (3, 4):
                if not retain:
                    erase.putpixel((i*8+5, y), 255)
                    result.putpixel((i*8+5, y), (0, 0, 0, 0))
            for y in range(3, bottom+1):
                for x in range(2, 5):
                    p = (i*8+x, y)
                    costume.putpixel(p, 255)
                    materials.putpixel(p, 1)
                    result.putpixel(p, color)
        for suffix, im in (("erase", erase), ("costume", costume), ("materials", materials), ("sheet", result)):
            im.save(out / f"{name}-{suffix}.png")
        target = deepcopy(spec)
        target["sheet"] = f"{name}-sheet.png"
        write_json(out / f"{name}.json", target)
        write_json(out / f"{name}-appearance.json", {"schema_version": 1,
            "components": {"6": {"name": name, "kind": "costume"}},
            "source_policy": {"3": "replace", "4": "retain" if retain else "remove"},
            "layers": [{"name": "remove-satchel", "owner": 1, "mask": f"{name}-erase.png"},
                       {"name": name, "owner": 6, "mask": f"{name}-costume.png"}],
            "materials": {"fabric": hex_rgb(color[:3])}, "material_labels": {"1": "fabric"},
            "material_map": f"{name}-materials.png", "exceptions": [],
            "relations": [{"samples": [{"frame": f"step-{i}", "point": [2, 3]} for i in range(2)]}]})
    write_json(out / "sequences.json", {"source_structure_sha256": structure_digest(spec), "tick_hz": 60,
        "sequences": {name: {"loop": True, "steps": [{"frame": f"step-{i}", "ticks": 8, "flip_x": flip}
            for i in (0, 1, 0)]} for name, flip in (("west", False), ("east", True))}})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    source = args.out / "source"
    create_fixture(source)
    for name in ("coat", "vest"):
        review_bundle(source / "source.json", source / f"{name}.json", source / "template.json",
                      source / f"{name}-appearance.json", args.out / name, source / "sequences.json")


if __name__ == "__main__":
    main()
