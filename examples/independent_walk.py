# SPDX-License-Identifier: Apache-2.0
"""Independent original stocky mechanic, drawn without any semantic template.

This source fixture knows nothing about outfit anchors, regions or the courier.
All artwork is original geometric drawing under the repository's Apache license.
"""
from pathlib import Path

from PIL import Image, ImageDraw

from frametailor.pipeline import write_json


def create_source(out: Path) -> None:
    if out.exists():
        raise ValueError("choose a fresh source directory")
    out.mkdir(parents=True)
    sheet = Image.new("RGBA", (144, 32))
    frames = []
    # Independently authored poses: shifting hips, staggered shoulders and a
    # different arm path, rather than translating the first character's rig.
    for index, view in enumerate(("south", "north", "west")):
        for step in range(3):
            im = Image.new("RGBA", (16, 32))
            d = ImageDraw.Draw(im)
            ink, skin, hair = "#332c35", "#83574b", "#ddd5ba"
            hip = (0, -1, 1)[step]
            left = ((1, 21), (2, 20), (1, 22))[step]
            right = ((13, 20), (12, 22), (13, 21))[step]
            # Short broad torso, sloping shoulders and long, splayed boots.
            d.polygon([(3, 14), (11, 14), (13, 17), (12, 24), (3, 24), (2, 17)], fill=ink)
            d.rectangle((4, 15, 11, 23), fill="#c37842")
            d.rectangle((10, 16, 11, 23), fill="#975033")
            d.rectangle((4 + hip, 24, 6 + hip, 27), fill="#5b6462")
            d.rectangle((9 - hip, 24, 11 - hip, 27), fill="#3f4947")
            d.polygon([(4 + hip, 27), (6 + hip, 27), (6 + hip, 30), (2 + hip, 30)], fill=ink)
            d.polygon([(9 - hip, 27), (11 - hip, 27), (13 - hip, 29), (9 - hip, 29)], fill=ink)
            cuffs = (left, right) if view != "west" else (((10, 22), (11, 21), (10, 23))[step],)
            for x, y in cuffs:
                d.polygon([(x, 15), (x + 1, 16), (x + 1, y), (x - 1, y), (x - 1, 18)], fill=ink)
                d.rectangle((x, 17, x + 1, y), fill="#d29251")
                d.rectangle((x + 1, 18, x + 1, y - 1), fill="#aa693c")
                d.rectangle((x, y + 1, x + 1, y + 2), fill=skin)
            # Side torso is displaced forward; sleeve occludes its rear edge.
            if view == "west":
                d.rectangle((4, 15, 5, 23), fill=ink)
            # Smaller angular head, pale crest, broad ears, permanent goggles.
            d.rectangle((5, 7, 10, 12), fill=skin)
            d.rectangle((4, 8, 11, 10), fill=skin)
            d.rectangle((6, 13, 9, 14), fill=skin)
            d.polygon([(4, 7), (5, 4), (7, 6), (9, 3), (11, 7)], fill=hair)
            if view == "north":
                d.rectangle((5, 8, 10, 11), fill=hair)
            elif view == "west":
                d.rectangle((3, 9, 5, 10), fill="#e6bd62")
                d.point((3, 9), fill=ink)
                d.rectangle((10, 8, 10, 11), fill=hair)
            else:
                d.line((4, 9, 11, 9), fill=ink)
                d.rectangle((5, 9, 6, 10), fill="#e6bd62")
                d.rectangle((9, 9, 10, 10), fill="#e6bd62")
            slot = index * 3 + step
            sheet.paste(im, (slot * 16, 0))
            frames.append({"id": f"{view}-{step}", "rect": [slot * 16, 0, 16, 32],
                           "anchor": [7, 31], "duration_ms": (110, 170, 130)[step]})
    cycles = {view: [{"frame": f"{stored}-{step}", "duration_ms": ms, "flip_x": view == "east"}
                     for step, ms in ((0, 110), (1, 170), (0, 110), (2, 130))]
              for view, stored in (("south", "south"), ("north", "north"), ("west", "west"), ("east", "west"))}
    sheet.save(out / "sheet.png")
    write_json(out / "animation.json", {"schema_version": 1, "animation": "original-mechanic-walk",
               "sheet": "sheet.png", "loop": True, "frames": frames, "walk_cycles": cycles})
