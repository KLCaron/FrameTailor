# SPDX-License-Identifier: Apache-2.0
"""Regenerate the original outfit example; no external artwork or AI involved."""
from pathlib import Path
import argparse

from PIL import Image, ImageDraw

from frametailor.pipeline import preview, write_json


def create_fixture(out: Path) -> None:
    if out.exists():
        raise ValueError("choose a fresh output directory")
    out.mkdir(parents=True)
    (out / "frames").mkdir()
    sheet = Image.new("RGBA", (256, 64))
    mask = Image.new("L", sheet.size)
    frames, entries, landmarks = [], [], {}
    for i, (swing, bob) in enumerate(((-3, 0), (-1, -1), (3, 0), (1, -1))):
        frame = Image.new("RGBA", (64, 64))
        d = ImageDraw.Draw(frame)
        # Head with a single tuft and a cheek mark on screen right.
        d.rectangle((25, 8 + bob, 38, 23 + bob), fill="#d39b73")
        d.rectangle((24, 7 + bob, 38, 12 + bob), fill="#342a32")
        d.rectangle((24, 5 + bob, 28, 10 + bob), fill="#342a32")
        d.rectangle((28, 15 + bob, 29, 16 + bob), fill="#172c40")
        d.rectangle((35, 15 + bob, 36, 16 + bob), fill="#172c40")
        d.point((37, 19 + bob), fill="#843e54")
        # Short plain shirt; the new coat may extend around its torso/hem.
        d.rectangle((24, 25 + bob, 39, 39 + bob), fill="#597c88")
        d.rectangle((20, 26 + bob, 23, 35 + bob), fill="#597c88")
        d.rectangle((40, 26 + bob, 43, 35 + bob), fill="#597c88")
        # Hands and one screen-left wristband are deliberately protected.
        d.rectangle((20 + swing, 36 + bob, 23 + swing, 40 + bob), fill="#d39b73")
        d.rectangle((40 - swing, 36 + bob, 43 - swing, 40 + bob), fill="#d39b73")
        d.line((20 + swing, 36 + bob, 23 + swing, 36 + bob), fill="#c84356", width=1)
        d.rectangle((25 + swing, 42, 29 + swing, 53), fill="#263b53")
        d.rectangle((34 - swing, 42, 38 - swing, 53), fill="#263b53")
        d.rectangle((24 + swing, 54, 30 + swing, 57), fill="#4f3347")
        d.rectangle((33 - swing, 54, 39 - swing, 57), fill="#4f3347")
        # Only torso/sleeves and a two-pixel hem expansion are editable.
        editable = Image.new("L", (64, 64))
        m = ImageDraw.Draw(editable)
        m.rectangle((22, 24 + bob, 41, 41 + bob), fill=255)
        m.rectangle((19, 25 + bob, 44, 35 + bob), fill=255)
        # Keep every hand/wrist pixel outside the permitted region.
        for x in (20 + swing, 40 - swing):
            m.rectangle((x, 36 + bob, x + 3, 40 + bob), fill=0)
        mask.paste(editable, (i * 64, 0))
        sheet.paste(frame, (i * 64, 0))
        frame.save(out / "frames" / f"{i:04d}.png")
        frames.append(frame)
        name = f"walk-{i}"
        entries.append({"id": name, "rect": [i * 64, 0, 64, 64],
                        "duration_ms": [120, 80, 110, 90][i], "anchor": [32, 58]})
        landmarks[name] = {"head": [32, 16 + bob],
                           "screen_left_hand": [21 + swing, 38 + bob],
                           "screen_right_hand": [41 - swing, 38 + bob],
                           "screen_left_heel": [27 + swing, 57],
                           "screen_right_heel": [36 - swing, 57]}
    # Outfit reference: same original subject, a genuinely different garment.
    reference = frames[0].copy()
    d = ImageDraw.Draw(reference)
    d.rectangle((20, 26, 43, 35), fill="#694b95")
    d.polygon([(24, 25), (39, 25), (41, 41), (22, 41)], fill="#694b95")
    d.polygon([(25, 24), (31, 27), (29, 30), (24, 26)], fill="#f4cf67")
    d.polygon([(38, 24), (32, 27), (34, 30), (39, 26)], fill="#f4cf67")
    d.line((31, 28, 31, 40), fill="#30243e", width=1)
    d.rectangle((35, 32, 38, 36), fill="#4cafac")  # single screen-right pocket
    d.rectangle((22, 28, 23, 29), fill="#ed8755")  # screen-left shoulder badge
    sheet.save(out / "sheet.png")
    reference.save(out / "outfit-reference.png")
    mask.save(out / "editable-mask.png")
    spec = {"schema_version": 1, "sheet": "sheet.png", "animation": "explorer-walk",
            "loop": True, "frames": entries}
    write_json(out / "animation.json", spec)
    write_json(out / "acceptance.json", {
        "profile": "outfit-regions-v1", "editable_mask": "editable-mask.png",
        "mask_convention": "255 permits RGB/alpha changes; 0 requires exact RGBA preservation",
        "target": "Violet flared jacket, gold collar, one teal pocket on screen right, orange badge on screen left",
        "pose_landmarks": landmarks,
        "review_checks": [
            "Keep frame identity/order, timing, anchors and all four source poses; do not mirror or realign",
            "Head, tuft, cheek mark, wristband, hands and boots remain at source coordinates",
            "New collar, pocket, badge and jacket cut must be consistent in every frame",
            "Outfit must change garment construction, not just recolor the original shirt",
            "Silhouette changes are confined to white mask pixels; no cape, hat or foot movement",
            "Inspect loop playback for movement and clothing flicker; inspect all frame pairs",
            "Require successful provider job, exact response count/order, recorded cost/seed and no fallbacks",
            "Reject missing, unchanged, substituted or duplicated frames; structural success is not approval",
        ],
    })
    preview(frames, spec, out / "preview.png")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    create_fixture(parser.parse_args().out)
