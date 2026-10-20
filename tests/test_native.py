# SPDX-License-Identifier: Apache-2.0
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from PIL import Image, ImageOps

from examples.native_walk import create_fixture, review
from frametailor.native import native_outfit, regions_from_rules, render
from frametailor.pipeline import digest, load, structure, write_json


class NativeOutfitTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.source = self.root / "source"
        create_fixture(self.source)
        self.manifest = self.source / "animation.json"
        self.template = self.source / "template.json"
        self.outfit = self.source / "jacket.json"

    def test_two_structural_outfits_reuse_template_and_preserve_source(self):
        hashes = {p.name: digest(p) for p in self.source.iterdir()}
        spec, before, frames = load(self.manifest)
        outputs = []
        for name in ("jacket", "overalls"):
            out = self.root / name
            report = native_outfit(self.manifest, self.template, self.source / f"{name}.json", out)
            self.assertTrue(report["ok"])
            self.assertFalse(report["approved"])
            self.assertEqual(report["frame_exception_count"], 0)
            self.assertLessEqual(report["visible_colors"], 15)
            after_spec, after, changed = load(out / "animation.json")
            self.assertEqual(structure(spec), structure(after_spec))
            self.assertEqual(before.getchannel("A").tobytes(), after.getchannel("A").tobytes())
            with Image.open(self.source / "regions.png") as labels:
                self.assertTrue(all(a == b for a, b, label in zip(before.get_flattened_data(),
                    after.get_flattened_data(), labels.get_flattened_data()) if not label))
            self.assertEqual(len(changed), 9)
            self.assertTrue(all(im.size == (16, 32) for im in changed))
            self.assertTrue(all(record["source_color_splits"] > 0 for record in report["frames"]))
            self.assertTrue(all(a.tobytes() != b.tobytes() for a, b in zip(frames, changed)))
            checks = json.loads((out / "validation.json").read_text())
            self.assertTrue(all(c["ok"] for c in checks.values()))
            provenance = json.loads((out / "provenance.json").read_text())
            for path, value in provenance["artifacts_sha256"].items():
                self.assertEqual(digest(out / path), value)
            # The normalized bundled template can be replayed with portable inputs.
            replay, _, _ = render(out / "inputs/animation.json", out / "inputs/template.json", out / "inputs/outfit.json")
            self.assertEqual(replay.tobytes(), after.tobytes())
            outputs.append(after)
        self.assertNotEqual(outputs[0].tobytes(), outputs[1].tobytes())
        self.assertEqual(hashes, {p.name: digest(p) for p in self.source.iterdir()})

    def test_exact_details_follow_body_bob_and_cuffs_follow_arms(self):
        output, _, report = render(self.manifest, self.template, self.outfit)
        for index in range(3):
            bob = -1 if index == 0 else 0
            for y in range(17 + bob, 24 + bob):
                self.assertEqual(output.getpixel((16 * index + 7, y)), (255, 208, 120, 255))
            record = report["frames"][index]
            self.assertEqual(record["anchors"]["left_cuff"][1], 21 + bob + (0, -1, 1)[index])
        overalls, _, _ = render(self.manifest, self.template, self.source / "overalls.json")
        for x in (6, 9):
            self.assertEqual(overalls.getpixel((x, 18)), (39, 43, 60, 255))
        self.assertNotEqual(output.getpixel((7, 18)), overalls.getpixel((7, 18)))

    def test_deterministic_bundle_and_recorded_manual_exception(self):
        config = json.loads(self.template.read_text())
        config["poses"]["south-0"]["exceptions"] = ["Reviewed source anchor for this pose"]
        write_json(self.template, config)
        for name in ("one", "two"):
            report = native_outfit(self.manifest, self.template, self.outfit, self.root / name)
            self.assertEqual(report["frame_exception_count"], 1)
        files = lambda root: {p.relative_to(root): digest(p) for p in root.rglob("*") if p.is_file()}
        self.assertEqual(files(self.root / "one"), files(self.root / "two"))
        with self.assertRaisesRegex(ValueError, "already exists"):
            native_outfit(self.manifest, self.template, self.outfit, self.root / "one")

    def test_source_and_metadata_binding_prevent_template_drift(self):
        spec = json.loads(self.manifest.read_text())
        original = deepcopy(spec)
        spec["walk_cycles"]["east"][0]["flip_x"] = False
        write_json(self.manifest, spec)
        with self.assertRaisesRegex(ValueError, "does not match"):
            render(self.manifest, self.template, self.outfit)
        write_json(self.manifest, original)
        image = Image.open(self.source / "sheet.png").convert("RGBA")
        image.putpixel((0, 0), (1, 2, 3, 0))
        image.save(self.source / "sheet.png")
        with self.assertRaisesRegex(ValueError, "does not match"):
            render(self.manifest, self.template, self.outfit)

    def test_refuses_protected_or_out_of_frame_details_without_publishing(self):
        original = json.loads(self.outfit.read_text())
        for point in ([0, -5, "trim.base"], [40, 0, "trim.base"]):
            config = deepcopy(original)
            config["views"]["south"][0]["pixels"] = [point]
            write_json(self.outfit, config)
            with self.assertRaisesRegex(ValueError, "outside declared garment"):
                native_outfit(self.manifest, self.template, self.outfit, self.root / "bad")
            self.assertFalse((self.root / "bad").exists())

    def test_refuses_invisible_overlapping_and_palette_only_details(self):
        original = json.loads(self.outfit.read_text())
        config = deepcopy(original)
        config["materials"]["trim"]["base"] = config["materials"]["torso"]["base"]
        write_json(self.outfit, config)
        with self.assertRaisesRegex(ValueError, "no contrast"):
            render(self.manifest, self.template, self.outfit)
        config = deepcopy(original)
        config["views"]["south"][0]["pixels"].append(config["views"]["south"][0]["pixels"][0])
        write_json(self.outfit, config)
        with self.assertRaisesRegex(ValueError, "overlapping"):
            render(self.manifest, self.template, self.outfit)
        # Cover each visible source color uniformly: a detail name cannot disguise recoloring.
        labels = Image.open(self.source / "regions.png")
        _, sheet, _ = load(self.manifest)
        config = deepcopy(original)
        template = json.loads(self.template.read_text())
        for k in template["labels"]:
            template["labels"][k] = "torso.base"
        template["poses"]["south-0"]["anchors"]["origin"] = [0, 0]
        # A one-pixel pattern with no source-color split is tested using uniform output.
        # Other poses are irrelevant: the first frame must fail before continuing.
        points = [[x, y, "trim.base"] for y in range(32) for x in range(16)
                  if labels.getpixel((x, y)) and sheet.getpixel((x, y))[:3] == (101, 134, 107)]
        config["views"]["south"] = [{"name": "uniform original green mapping", "anchor": "origin",
                                      "regions": list(range(1, 7)), "pixels": points}]
        # Retained pants/shadow colors would also split if remapped differently; assign
        # all their source pixels to the same target as their protected counterparts.
        for k in ("5", "6"):
            template["labels"][k] = "legs.base" if k == "5" else "legs.shade"
        write_json(self.template, template)
        write_json(self.outfit, config)
        with self.assertRaisesRegex(ValueError, "palette recoloring alone"):
            render(self.manifest, self.template, self.outfit)

    def test_palette_budget_region_labels_and_missing_views(self):
        config = json.loads(self.outfit.read_text())
        config["max_visible_colors"] = 2
        write_json(self.outfit, config)
        with self.assertRaisesRegex(ValueError, "palette budget"):
            render(self.manifest, self.template, self.outfit)
        del config["views"]["west"]
        write_json(self.outfit, config)
        with self.assertRaisesRegex(ValueError, "every pose view"):
            render(self.manifest, self.template, self.outfit)
        labels = Image.open(self.source / "regions.png")
        labels.putpixel((0, 0), 99)
        labels.save(self.source / "regions.png")
        with self.assertRaisesRegex(ValueError, "undefined region"):
            render(self.manifest, self.template, self.outfit)

    def test_transparent_regions_missing_poses_and_invalid_native_coordinates(self):
        original = json.loads(self.template.read_text())
        config = deepcopy(original)
        del config["poses"]["north-2"]
        write_json(self.template, config)
        with self.assertRaisesRegex(ValueError, "exactly the source frame ids"):
            render(self.manifest, self.template, self.outfit)
        config = deepcopy(original)
        config["poses"]["south-0"]["anchors"]["chest"] = [5.5, 16]
        write_json(self.template, config)
        with self.assertRaisesRegex(ValueError, "integer anchors"):
            render(self.manifest, self.template, self.outfit)
        write_json(self.template, original)
        with Image.open(self.source / "regions.png") as image:
            labels = image.copy()
        labels.putpixel((0, 0), 1)
        labels.save(self.source / "regions.png")
        with self.assertRaisesRegex(ValueError, "transparent source"):
            render(self.manifest, self.template, self.outfit)

    def test_directional_previews_use_references_timing_and_per_character_mirror(self):
        native_outfit(self.manifest, self.template, self.outfit, self.root / "jacket")
        review(self.manifest, [self.root / "jacket/animation.json"], self.root)
        spec, _, source = load(self.manifest)
        changed = load(self.root / "jacket/animation.json")[2]
        for direction, cycle in spec["walk_cycles"].items():
            with Image.open(self.root / f"walking-{direction}-1x.png") as animation:
                self.assertEqual(animation.n_frames, 4)
                self.assertEqual(animation.info["loop"], 0)
                for i, item in enumerate(cycle):
                    animation.seek(i)
                    self.assertEqual(animation.info["duration"], 140)
                    index = next(j for j, entry in enumerate(spec["frames"]) if entry["id"] == item["frame"])
                    for col, collection in enumerate((source, changed)):
                        frame = collection[index]
                        if item["flip_x"]:
                            frame = ImageOps.mirror(frame)
                        expected = Image.new("RGB", (16, 32), "#9298a1")
                        expected.paste(frame, (0, 0), frame)
                        actual = animation.convert("RGB").crop((col * 16, 0, (col + 1) * 16, 32))
                        self.assertEqual(expected.tobytes(), actual.tobytes())

    def test_installed_command_interface(self):
        result = subprocess.run([sys.executable, "-m", "frametailor", "outfit", str(self.manifest),
            "--template", str(self.template), "--outfit", str(self.outfit), "--out", str(self.root / "cli")],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(json.loads(result.stdout)["ok"])

    def test_color_and_geometry_ownership_is_explicit_and_order_independent(self):
        sheet = Image.new("RGBA", (4, 2), "#65866b")
        sheet.putpixel((3, 1), (101, 134, 107, 0))
        rules = [{"rect": [0, 0, 2, 1], "colors": {"#65866b": 1}},
                 {"rect": [2, 1, 2, 1], "colors": {"#65866b": 2}}]
        labels = regions_from_rules(sheet, rules)
        self.assertEqual(list(labels.get_flattened_data()), [1, 1, 0, 0, 0, 0, 2, 0])
        self.assertEqual(labels.tobytes(), regions_from_rules(sheet, rules[::-1]).tobytes())
        for bad, error in (([{"rect": [0, 0, 1, 1], "colors": {"#65866b": 2}}], "conflicting"),
                           ([{"rect": [0, 0, 5, 1], "colors": {"#65866b": 1}}], "outside"),
                           ([{"rect": [0, 0, True, 1], "colors": {"#65866b": 1}}], "ownership rect"),
                           ([{"rect": [0, 0, 1, 1], "colors": {"#ffffff": 1}}], "absent")):
            with self.assertRaisesRegex(ValueError, error):
                regions_from_rules(sheet, rules + bad)

    def test_pose_occlusion_preserves_exact_source_and_replays_from_bundle(self):
        template = json.loads(self.template.read_text())
        template["poses"]["south-0"]["occluders"] = {"front_part": [[7, 17]]}
        template["poses"]["south-0"]["exceptions"] = ["Front part covers the zipper in this pose"]
        write_json(self.template, template)
        with Image.open(self.source / "regions.png") as image:
            labels = image.copy()
        labels.putpixel((7, 17), 0)
        labels.save(self.source / "regions.png")
        outfit = json.loads(self.outfit.read_text())
        outfit["views"]["south"][1]["occluded_by"] = ["front_part"]
        write_json(self.outfit, outfit)
        before = load(self.manifest)[1]
        report = native_outfit(self.manifest, self.template, self.outfit, self.root / "occluded")
        after = load(self.root / "occluded/animation.json")[1]
        self.assertEqual(after.getpixel((7, 17)), before.getpixel((7, 17)))
        self.assertEqual(report["occluded_pixel_count"], 1)
        self.assertEqual(report["frames"][0]["details"][1]["occluded_pixels"], [[7, 17]])
        self.assertEqual(report["frames"][1]["details"][1]["occluded_pixels"], [])
        replay = render(self.root / "occluded/inputs/animation.json",
                        self.root / "occluded/inputs/template.json", self.root / "occluded/inputs/outfit.json")[0]
        self.assertEqual(replay.tobytes(), after.tobytes())
        # A completely hidden detail is valid; invalid roles and overlaps still fail.
        outfit["views"]["south"][1]["pixels"] = [[2, 2, "trim.base"]]
        write_json(self.outfit, outfit)
        self.assertEqual(render(self.manifest, self.template, self.outfit)[2]["occluded_pixel_count"], 1)
        for pixels, error in (([[2, 2, "undefined.base"]], "undefined/invalid"),
                              ([[2, 2, "trim.base"], [2, 2, "trim.base"]], "overlapping"),
                              ([[2, 2, "trim.base"], [2, 3, "torso.base"]], "no contrast")):
            outfit["views"]["south"][1]["pixels"] = pixels
            write_json(self.outfit, outfit)
            with self.assertRaisesRegex(ValueError, error):
                render(self.manifest, self.template, self.outfit)

    def test_occlusion_cannot_authorize_edits_or_hide_invalid_details(self):
        template = json.loads(self.template.read_text())
        for points in ([[7, 17]], [[0, 0]], [[16, 17]], [[7.5, 17]], [[7, 17], [7, 17]]):
            config = deepcopy(template)
            config["poses"]["south-0"]["occluders"] = {"front_part": points}
            write_json(self.template, config)
            with self.assertRaisesRegex(ValueError, "occluder"):
                native_outfit(self.manifest, self.template, self.outfit, self.root / "invalid")
            self.assertFalse((self.root / "invalid").exists())
        write_json(self.template, template)
        outfit = json.loads(self.outfit.read_text())
        outfit["views"]["south"][0]["occluded_by"] = ["misspelled"]
        write_json(self.outfit, outfit)
        with self.assertRaisesRegex(ValueError, "unknown source occluder"):
            render(self.manifest, self.template, self.outfit)
