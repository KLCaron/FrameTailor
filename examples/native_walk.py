# SPDX-License-Identifier: Apache-2.0
"""Original 16x32 courier, one semantic template, two native-grid garments.

All art is geometric source code, independent of external character assets.
Output is a local review artifact; no generated sheets need to be checked in.
"""
import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps

from frametailor.native import native_outfit, structure_digest
from frametailor.pipeline import digest, load, write_json


def _detail(name, anchor, regions, points, role):
    return {"name": name, "anchor": anchor, "regions": regions,
            "pixels": [[x, y, role] for x, y in points]}


def outfits() -> dict:
    """Patterns are authored once per view, never once per output frame."""
    def torso(name, points, role):
        return _detail(name, "chest", [1, 2], points, role)

    jacket = {
        "south": [torso("folded collar", [(0, 0), (1, 0), (1, 1), (4, 0), (5, 0), (4, 1)], "trim.base"),
                  torso("zipper", [(2, y) for y in range(1, 8)], "trim.base"),
                  torso("pocket openings", [(0, 5), (1, 5), (4, 5), (5, 5)], "torso.shade")],
        "north": [torso("back yoke", [(x, 1) for x in range(6)], "trim.base"),
                  torso("back seam", [(2, y) for y in range(2, 8)], "torso.shade")],
        "west": [torso("side collar", [(0, 0), (1, 0), (1, 1)], "trim.base"),
                 torso("front zipper edge", [(0, y) for y in range(1, 8)], "trim.base")],
    }
    overalls = {
        "south": [torso("bib and straps", [(x, y) for y in range(7) for x in range(1, 5)
                                            if (y >= 3 or x in (1, 4)) and (x, y) not in ((1, 3), (4, 3))], "legs.base"),
                  torso("strap clasps", [(1, 3), (4, 3)], "fastener.base"),
                  torso("waist band", [(x, 7) for x in range(6)], "legs.shade")],
        "north": [torso("back braces", [(x, y) for y in range(7) for x in (1, 4)], "legs.base"),
                  torso("waist band", [(x, 7) for x in range(6)], "legs.shade")],
        "west": [torso("side bib and strap", [(1, y) for y in range(7) if y != 3]
                       + [(0, y) for y in range(4, 8)] + [(1, 7)], "legs.base"),
                 torso("side clasp", [(1, 3)], "fastener.base")],
    }
    for view, patterns in jacket.items():
        anchors = ("left_cuff", "right_cuff") if view != "west" else ("near_cuff",)
        for anchor in anchors:
            patterns.append(_detail("ribbed " + anchor, anchor, [3, 4], [(0, 0), (1, 0)], "trim.base"))
    return {
        "jacket": {"schema_version": 1, "name": "Blue zip jacket with gold collar and cuffs",
            "max_visible_colors": 15, "materials": {
                "torso": {"base": "#315d91", "shade": "#203a64"},
                "sleeves": {"base": "#315d91", "shade": "#203a64"},
                "legs": {"base": "#474b65", "shade": "#303249"},
                "trim": {"base": "#ffd078"}}, "views": jacket},
        "overalls": {"schema_version": 1, "name": "Ochre bib overalls over an ivory shirt",
            "max_visible_colors": 15, "materials": {
                "torso": {"base": "#f4e6c5", "shade": "#c2b294"},
                "sleeves": {"base": "#f4e6c5", "shade": "#c2b294"},
                "legs": {"base": "#cc8844", "shade": "#86522f"},
                "fastener": {"base": "#272b3c"}}, "views": overalls},
    }


def create_fixture(out: Path) -> None:
    if out.exists():
        raise ValueError("choose a fresh fixture directory")
    out.mkdir(parents=True)
    sheet, regions = Image.new("RGBA", (144, 32)), Image.new("L", (144, 32))
    entries, poses = [], {}
    colors = {0: "#272b3c", 1: "#65866b", 2: "#47664e", 3: "#65866b",
              4: "#47664e", 5: "#474b65", 6: "#303249"}
    for index, (view, step) in enumerate((v, s) for v in ("south", "north", "west") for s in (0, 1, 2)):
        frame, labels = Image.new("RGBA", (16, 32)), Image.new("L", (16, 32))
        d, m = ImageDraw.Draw(frame), ImageDraw.Draw(labels)
        bob = -1 if step == 0 else 0
        arm = (0, -1, 1)[step]

        def rect(box, label=0, color=None):
            d.rectangle(box, fill=color or colors[label])
            m.rectangle(box, fill=label)

        # Feet/legs and garment edges remain in the source animation geometry.
        for px, shift in ((5, arm), (9, -arm)):
            rect((px, 24 + bob, px + 1, 28 + shift), 5)
            rect((px, 25 + bob, px, 28 + shift), 6)
            rect((px - 1, 29 + shift, px + 1, 29 + shift), color="#493b45")
        rect((4, 15 + bob, 11, 24 + bob))
        rect((5, 16 + bob, 10, 23 + bob), 1)
        rect((10, 17 + bob, 10, 23 + bob), 2)
        cuffs = {}
        for px, shift, anchor in ((2, arm, "left_cuff"), (12, -arm, "right_cuff")):
            if view == "west":
                if px == 2:
                    continue
                px, shift, anchor = 8, arm, "near_cuff"
            bottom = 21 + bob + shift
            rect((px - 1, 17 + bob + shift, px + 2, bottom + 1))
            rect((px, 17 + bob + shift, px + 1, bottom), 3)
            rect((px + 1, 18 + bob + shift, px + 1, bottom), 4)
            rect((px, bottom + 1, px + 1, bottom + 2), color="#dba27b")
            cuffs[anchor] = [px, bottom]
        # Original round head, curly hair, nose and asymmetric cheek freckle.
        rect((4, 6 + bob, 11, 13 + bob), color="#dba27b")
        rect((5, 5 + bob, 10, 5 + bob), color="#583b37")
        rect((3, 7 + bob, 12, 9 + bob), color="#583b37")
        rect((4, 6 + bob, 5, 7 + bob), color="#583b37")
        rect((9, 6 + bob, 11, 7 + bob), color="#583b37")
        rect((6, 14 + bob, 9, 15 + bob), color="#dba27b")
        if view == "north":
            rect((4, 10 + bob, 11, 12 + bob), color="#583b37")
        elif view == "west":
            rect((3, 11 + bob, 4, 12 + bob), color="#dba27b")
            rect((4, 10 + bob, 4, 10 + bob))
            rect((10, 10 + bob, 11, 12 + bob), color="#583b37")
        else:
            rect((5, 10 + bob, 5, 10 + bob))
            rect((10, 10 + bob, 10, 10 + bob))
            rect((10, 12 + bob, 10, 12 + bob), color="#af5b58")
        name = f"{view}-{step}"
        sheet.paste(frame, (index * 16, 0))
        regions.paste(labels, (index * 16, 0))
        entries.append({"id": name, "rect": [index * 16, 0, 16, 32], "anchor": [8, 32], "duration_ms": 140})
        poses[name] = {"view": view, "anchors": {"chest": [5, 16 + bob], **cuffs}, "exceptions": []}
    cycles = {view: [{"frame": f"{stored}-{step}", "duration_ms": 140, "flip_x": view == "east"}
                     for step in (1, 0, 2, 0)]
              for view, stored in (("south", "south"), ("north", "north"), ("west", "west"), ("east", "west"))}
    spec = {"schema_version": 1, "animation": "original-courier-walk", "sheet": "sheet.png", "loop": True,
            "frames": entries, "walk_cycles": cycles}
    sheet.save(out / "sheet.png")
    regions.save(out / "regions.png")
    write_json(out / "animation.json", spec)
    write_json(out / "template.json", {"schema_version": 1, "source_sheet_sha256": digest(out / "sheet.png"),
        "source_structure_sha256": structure_digest(spec), "regions": "regions.png",
        "labels": {"1": "torso.base", "2": "torso.shade", "3": "sleeves.base", "4": "sleeves.shade",
                   "5": "legs.base", "6": "legs.shade"}, "poses": poses})
    for name, config in outfits().items():
        write_json(out / f"{name}.json", config)


def review(source: Path, outputs: list[Path], out: Path) -> None:
    """Comparison rows: source/outfits; APNG columns: source/outfits."""
    spec, _, original = load(source)
    collections = [original] + [load(p)[2] for p in outputs]
    canvas = Image.new("RGB", (len(original) * 16, len(collections) * 32), "#9298a1")
    for row, frames in enumerate(collections):
        for col, frame in enumerate(frames):
            canvas.paste(frame, (col * 16, row * 32), frame)
    canvas.save(out / "comparison-native.png")
    canvas.resize((canvas.width * 10, canvas.height * 10), Image.Resampling.NEAREST).save(out / "comparison-10x.png")
    for direction, cycle in spec["walk_cycles"].items():
        panels = []
        for item in cycle:
            index = next(i for i, entry in enumerate(spec["frames"]) if entry["id"] == item["frame"])
            panel = Image.new("RGB", (len(collections) * 16, 32), "#9298a1")
            for col, frames in enumerate(collections):
                frame = ImageOps.mirror(frames[index]) if item["flip_x"] else frames[index]
                panel.paste(frame, (col * 16, 0), frame)
            panels.append(panel)
        for scale in (1, 10):
            frames = [p.resize((p.width * scale, p.height * scale), Image.Resampling.NEAREST) for p in panels]
            frames[0].save(out / f"walking-{direction}-{scale}x.png", save_all=True, append_images=frames[1:],
                           duration=[item["duration_ms"] for item in cycle], loop=0, disposal=0, blend=0)
    write_json(out / "layout.json", {"comparison_rows_and_animation_columns": ["source", *[p.parent.name for p in outputs]],
        "stored_columns": [entry["id"] for entry in spec["frames"]], "playback": spec["walk_cycles"]})


def pilot(out: Path) -> None:
    if out.exists():
        raise ValueError("choose a fresh pilot directory")
    source = out / "source"
    create_fixture(source)
    for name in outfits():
        native_outfit(source / "animation.json", source / "template.json", source / f"{name}.json", out / name)
    review(source / "animation.json", [out / name / "animation.json" for name in outfits()], out)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    pilot(parser.parse_args().out)
