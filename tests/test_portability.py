# SPDX-License-Identifier: Apache-2.0
import json
from pathlib import Path
import tempfile
import unittest

from PIL import Image, ImageOps

from examples.portable_walk import pilot
from frametailor.native import regions_from_colors, render
from frametailor.pipeline import digest, load, structure


class RegionAuthoringTests(unittest.TestCase):
    def test_explicit_palette_annotation_preserves_input_and_protects_unmapped_pixels(self):
        source = Image.new("RGBA", (4, 1))
        source.putdata([(12, 34, 56, 255), (12, 34, 56, 100),
                        (12, 34, 56, 0), (99, 88, 77, 255)])
        before = source.tobytes()
        labels = regions_from_colors(source, {"#0c2238": 7})
        self.assertEqual(labels.mode, "L")
        self.assertEqual(list(labels.get_flattened_data()), [7, 7, 0, 0])
        self.assertEqual(source.tobytes(), before)
        self.assertEqual(labels.tobytes(), regions_from_colors(source, {"#0c2238": 7}).tobytes())
        # Explicit palette annotation works on independently indexed source art too.
        indexed = Image.new("P", (2, 1))
        indexed.putpalette([12, 34, 56, 99, 88, 77] + [0] * 762)
        indexed.putdata([0, 1])
        indexed.info["transparency"] = 0
        self.assertEqual(list(regions_from_colors(indexed, {"#63584d": 3}).get_flattened_data()), [0, 3])

    def test_refuses_typos_invalid_ids_and_duplicate_rgb(self):
        source = Image.new("RGB", (1, 1), "#abcdef")
        for mapping in ({}, {"red": 1}, {"#abcdef": 0}, {"#abcdef": 256}, {"#abcdef": True},
                        {"#abcdef": 1, "#ABCDEF": 2}, {"#123456": 1}):
            with self.subTest(mapping=mapping), self.assertRaises(ValueError):
                regions_from_colors(source, mapping)


class PortabilityTests(unittest.TestCase):
    def test_same_outfit_independent_sources_replay_and_second_outfit(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pilot(root / "one")
            pilot(root / "two")
            files = lambda path: {p.relative_to(path).as_posix(): digest(p)
                                  for p in path.rglob("*") if p.is_file()}
            self.assertEqual(files(root / "one"), files(root / "two"))
            jacket = root / "one/courier/source/jacket.json"
            self.assertNotEqual(digest(root / "one/courier/source/sheet.png"),
                                digest(root / "one/mechanic/source/sheet.png"))
            for name in ("courier", "mechanic"):
                base = root / "one" / name
                spec, before, _ = load(base / "source/animation.json")
                after_spec, after, _ = load(base / "jacket/animation.json")
                self.assertEqual(structure(spec), structure(after_spec))
                self.assertEqual(before.getchannel("A").tobytes(), after.getchannel("A").tobytes())
                with Image.open(base / "source/regions.png") as labels:
                    self.assertTrue(all(a == b for a, b, label in zip(before.get_flattened_data(),
                        after.get_flattened_data(), labels.get_flattened_data()) if not label))
                self.assertEqual(digest(jacket), digest(base / "jacket/inputs/outfit.json"))
                inputs = base / "jacket/inputs"
                replay, _, report = render(inputs / "animation.json", inputs / "template.json", inputs / "outfit.json")
                self.assertEqual(replay.tobytes(), after.tobytes())
                self.assertFalse(report["approved"])
                self.assertEqual(report["detail_pixel_count"], 147)
                self.assertEqual(report["frame_exception_count"], 0)
                for path, value in json.loads((base / "jacket/provenance.json").read_text())["artifacts_sha256"].items():
                    self.assertEqual(digest(base / "jacket" / path), value)
                # A different existing garment reuses the new template untouched.
                original_hashes = files(base / "source")
                overalls, _, _ = render(base / "source/animation.json", base / "source/template.json",
                                       root / "one/courier/source/overalls.json")
                self.assertNotEqual(overalls.tobytes(), after.tobytes())
                self.assertEqual(original_hashes, files(base / "source"))

    def test_directional_animation_pixels_flips_and_character_specific_timing(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "pilot"
            pilot(root)
            for name in ("courier", "mechanic"):
                base = root / name
                spec, _, original = load(base / "source/animation.json")
                changed = load(base / "jacket/animation.json")[2]
                for direction, cycle in spec["walk_cycles"].items():
                    with Image.open(base / f"walking-{direction}-1x.png") as animation:
                        self.assertEqual(animation.n_frames, len(cycle))
                        for i, item in enumerate(cycle):
                            animation.seek(i)
                            self.assertEqual(animation.info["duration"], item["duration_ms"])
                            index = next(j for j, f in enumerate(spec["frames"]) if f["id"] == item["frame"])
                            for col, collection in enumerate((original, changed)):
                                frame = collection[index]
                                if item["flip_x"]:
                                    frame = ImageOps.mirror(frame)
                                expected = Image.new("RGB", (16, 32), "#9298a1")
                                expected.paste(frame, (0, 0), frame)
                                self.assertEqual(expected.tobytes(), animation.convert("RGB").crop(
                                    (col * 16, 0, (col + 1) * 16, 32)).tobytes())
