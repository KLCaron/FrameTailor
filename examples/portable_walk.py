# SPDX-License-Identifier: Apache-2.0
"""Two independently drawn characters, one unchanged jacket. Local output only."""
import argparse
from pathlib import Path

from examples.independent_walk import create_source
from examples.native_walk import create_fixture, review
from frametailor.native import native_outfit, regions_from_colors, structure_digest
from frametailor.pipeline import digest, load, write_json


def author_template(source: Path) -> None:
    """Source-specific annotation, deliberately separate from source drawing.

    Six explicit color assignments replace nine painted masks. All 24 anchor
    positions below are manual placements; this does not infer anatomy.
    """
    spec, sheet, _ = load(source / "animation.json")
    regions = regions_from_colors(sheet, {"#c37842": 1, "#975033": 2, "#d29251": 3,
                                         "#aa693c": 4, "#5b6462": 5, "#3f4947": 6})
    regions.save(source / "regions.png")
    positions = {
        "south-0": {"chest": [5, 15], "left_cuff": [1, 21], "right_cuff": [13, 20]},
        "south-1": {"chest": [5, 15], "left_cuff": [2, 20], "right_cuff": [12, 22]},
        "south-2": {"chest": [5, 15], "left_cuff": [1, 22], "right_cuff": [13, 21]},
        "north-0": {"chest": [5, 15], "left_cuff": [1, 21], "right_cuff": [13, 20]},
        "north-1": {"chest": [5, 15], "left_cuff": [2, 20], "right_cuff": [12, 22]},
        "north-2": {"chest": [5, 15], "left_cuff": [1, 22], "right_cuff": [13, 21]},
        "west-0": {"chest": [6, 15], "near_cuff": [10, 22]},
        "west-1": {"chest": [6, 15], "near_cuff": [11, 21]},
        "west-2": {"chest": [6, 15], "near_cuff": [10, 23]},
    }
    write_json(source / "template.json", {"schema_version": 1,
        "source_sheet_sha256": digest(source / "sheet.png"),
        "source_structure_sha256": structure_digest(spec), "regions": "regions.png",
        "labels": {"1": "torso.base", "2": "torso.shade", "3": "sleeves.base",
                   "4": "sleeves.shade", "5": "legs.base", "6": "legs.shade"},
        "poses": {name: {"view": name.split("-")[0], "anchors": anchors, "exceptions": []}
                  for name, anchors in positions.items()}})


def pilot(out: Path) -> None:
    if out.exists():
        raise ValueError("choose a fresh pilot directory")
    courier, mechanic = out / "courier/source", out / "mechanic/source"
    create_fixture(courier)
    create_source(mechanic)
    author_template(mechanic)
    jacket = courier / "jacket.json"
    results = {}
    for name, source in (("courier", courier), ("mechanic", mechanic)):
        target = out / name / "jacket"
        results[name] = native_outfit(source / "animation.json", source / "template.json", jacket, target)
        review(source / "animation.json", [target / "animation.json"], out / name)
        assert digest(target / "inputs/outfit.json") == digest(jacket)
    write_json(out / "reuse.json", {"outfit_sha256": digest(jacket), "outfit_modified": False,
        "new_character": {"source_poses": 9, "color_to_region_assignments": 6,
            "poses_with_manually_painted_regions": 0, "poses_requiring_region_review": 9,
            "manual_anchor_placements": 24, "view_specific_outfit_adjustments": 0,
            "frame_specific_exceptions": 0, "manual_output_pixel_corrections": 0},
        "limits": ["Palette roles must be unambiguous across all source poses.",
            "Fixed detail geometry must fit the new body and occlusion in each view.",
            "All anchors are manually placed; helper does not infer anatomy.",
            "Structural success is separate from visual approval."], "results": results})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    pilot(parser.parse_args().out)
