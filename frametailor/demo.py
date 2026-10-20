# SPDX-License-Identifier: Apache-2.0
"""Original geometric robot fixture; no downloaded artwork or model output."""
from pathlib import Path

from PIL import Image, ImageDraw

from .pipeline import write_json


def create_demo(out: Path) -> None:
    if out.exists():
        raise ValueError("demo directory already exists; choose a fresh path")
    out.mkdir(parents=True)
    sheet = Image.new("RGBA", (96, 24))
    entries = []
    for i, (left, right, bob) in enumerate(((0, 0, 0), (-2, 2, -1), (0, 0, 0), (2, -2, -1))):
        frame = Image.new("RGBA", (24, 24))
        d = ImageDraw.Draw(frame)
        d.rectangle((7, 3 + bob, 16, 11 + bob), fill="#263445")
        d.rectangle((8, 4 + bob, 15, 10 + bob), fill="#3b82f6")
        d.rectangle((9, 6 + bob, 10, 7 + bob), fill="#f8fafc")
        d.rectangle((13, 6 + bob, 14, 7 + bob), fill="#f8fafc")
        d.rectangle((7, 12 + bob, 16, 17 + bob), fill="#1d4ed8")
        d.rectangle((4, 12 + bob, 6, 16 + bob), fill="#3b82f6")
        d.rectangle((17, 12 + bob, 19, 16 + bob), fill="#3b82f6")
        d.rectangle((8 + left, 18, 10 + left, 21), fill="#263445")
        d.rectangle((13 + right, 18, 15 + right, 21), fill="#263445")
        sheet.paste(frame, (i * 24, 0))
        entries.append({"id": f"step-{i}", "rect": [i * 24, 0, 24, 24],
                        "duration_ms": [120, 80, 120, 80][i], "anchor": [12, 22]})
    sheet.save(out / "sheet.png")
    write_json(out / "animation.json", {"schema_version": 1, "sheet": "sheet.png",
               "animation": "robot-walk", "loop": True, "frames": entries})
    write_json(out / "transform.json", {"type": "palette", "colors": {
        "#3b82f6": "#f59e0b", "#1d4ed8": "#b45309"}})
