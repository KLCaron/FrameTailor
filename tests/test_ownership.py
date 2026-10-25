# SPDX-License-Identifier: Apache-2.0
from copy import deepcopy
from fractions import Fraction
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest

from PIL import Image

from examples.ownership_review import create_fixture
from frametailor.ownership import validate_ownership
from frametailor.palette import bgr555, plan_palette, verify_native
from frametailor.pipeline import digest, write_json
from frametailor.review import review_bundle, timed_apng


class OwnershipTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.data = self.root / "source"
        create_fixture(self.data)
        self.source = self.data / "source.json"
        self.target = self.data / "coat.json"
        self.template = self.data / "template.json"
        self.appearance = self.data / "coat-appearance.json"

    def check(self):
        return validate_ownership(self.source, self.target, self.template, self.appearance)

    def edit_art(self, point, color):
        im = Image.open(self.data / "coat-sheet.png").convert("RGBA")
        im.putpixel(point, color)
        im.save(self.data / "coat-sheet.png")

    def test_two_costumes_reuse_source_facts_with_different_accessory_policy_and_silhouette(self):
        hashes = {p.name: digest(p) for p in self.data.iterdir()}
        for name in ("coat", "vest"):
            report = validate_ownership(self.source, self.data / f"{name}.json", self.template,
                                        self.data / f"{name}-appearance.json")
            self.assertTrue(report["ok"], report["diagnostics"])
            self.assertFalse(report["approved"])
            self.assertEqual(report["counts"], {})
        coat = Image.open(self.data / "coat-sheet.png")
        vest = Image.open(self.data / "vest-sheet.png")
        self.assertEqual(coat.getpixel((5, 3))[3], 0)
        self.assertEqual(vest.getpixel((5, 3))[3], 255)
        self.assertNotEqual(coat.getpixel((2, 5))[3], vest.getpixel((2, 5))[3])
        self.assertEqual(hashes, {p.name: digest(p) for p in self.data.iterdir()})

    def test_shared_rgb_bag_survives_but_identity_stays_valid(self):
        before = Image.open(self.data / "source.png")
        self.assertEqual(before.getpixel((3, 1)), before.getpixel((5, 3)))
        self.edit_art((5, 3), before.getpixel((5, 3)))
        report = self.check()
        self.assertFalse(report["ok"])
        self.assertEqual(report["counts"]["source_survivor"], 1)
        self.assertEqual(report["counts"]["unexpected_silhouette"], 1)
        self.assertNotIn("protected_rgba", report["counts"])
        self.assertEqual(next(d for d in report["diagnostics"] if d["check"] == "source_survivor")["point"], [5, 3])

    def test_unresolved_source_accessory_policy_and_owner_are_never_complete(self):
        config = json.loads(self.appearance.read_text())
        del config["source_policy"]["4"]
        write_json(self.appearance, config)
        with self.assertRaisesRegex(ValueError, "every source clothing/accessory"):
            self.check()
        config["source_policy"]["4"] = "remove"
        config["layers"] = config["layers"][1:]
        write_json(self.appearance, config)
        self.assertEqual(self.check()["counts"]["unresolved_replacement"], 4)
        owners = Image.open(self.data / "ownership.png")
        owners.putpixel((2, 1), 0)
        owners.save(self.data / "ownership.png")
        self.assertEqual(self.check()["counts"]["unresolved_ownership"], 1)

    def test_missing_hair_outline_and_interior_gaps_leave_legitimate_exterior(self):
        self.edit_art((3, 1), (0, 0, 0, 0))
        self.edit_art((3, 4), (0, 0, 0, 0))
        report = self.check()
        self.assertEqual(report["counts"]["missing_component_pixel"], 2)
        self.assertEqual(report["counts"]["protected_rgba"], 1)
        self.assertEqual(report["counts"]["new_enclosed_air"], 1)
        self.assertNotIn("unexpected_silhouette", report["counts"])
        self.assertFalse(any(d["point"] == [0, 0] for d in report["diagnostics"]))

    def test_decoded_rgb_drift_with_same_material_label_is_detected(self):
        self.edit_art((2, 3), (49, 90, 131, 255))
        report = self.check()
        self.assertEqual(report["counts"]["material_rgb"], 1)
        self.assertEqual(report["counts"]["pose_consistency"], 1)
        self.assertFalse(report["ok"])
        # Indexed PNGs are decoded to the same RGBA comparison space.
        im = Image.open(self.data / "coat-sheet.png").convert("RGBA")
        indexed = Image.new("P", im.size)
        palette = [(0, 0, 0), *sorted({p[:3] for p in im.get_flattened_data() if p[3]})]
        indexed.putpalette([v for c in palette for v in c] + [0]*(48-len(palette)*3))
        indexed.putdata([palette.index(p[:3]) if p[3] else 0 for p in im.get_flattened_data()])
        indexed.save(self.data / "coat-sheet.png", bits=4, transparency=0)
        self.assertEqual(self.check()["counts"], report["counts"])

    def test_material_lighting_exception_is_exact_reasoned_and_cannot_go_stale(self):
        self.edit_art((2, 3), (49, 90, 131, 255))
        config = json.loads(self.appearance.read_text())
        config["relations"] = []
        config["exceptions"] = [{"frame": "step-0", "point": [2, 3], "check": "material_rgb",
                                 "rgba": [49, 90, 131, 255], "reason": "Explicit state lighting sample"}]
        write_json(self.appearance, config)
        self.assertTrue(self.check()["ok"])
        self.assertEqual(len(self.check()["applied_exceptions"]), 1)
        self.edit_art((2, 3), (41, 90, 131, 255))
        self.assertEqual(self.check()["counts"]["stale_exception"], 1)

    def test_per_pose_foreground_costume_can_occlude_rod_but_not_source_hand_contact(self):
        mask = Image.open(self.data / "coat-costume.png")
        mask.putpixel((6, 2), 255)
        mask.save(self.data / "coat-costume.png")
        materials = Image.open(self.data / "coat-materials.png")
        materials.putpixel((6, 2), 1)
        materials.save(self.data / "coat-materials.png")
        self.edit_art((6, 2), (41, 90, 131, 255))
        self.assertEqual(self.check()["counts"]["protected_occlusion"], 1)
        config = json.loads(self.appearance.read_text())
        config["exceptions"] = [{"frame": "step-0", "point": [6, 2], "check": "protected_occlusion",
                                 "rgba": [41, 90, 131, 255], "reason": "Rod passes behind this costume pixel"}]
        write_json(self.appearance, config)
        self.assertTrue(self.check()["ok"])
        # Wrongly restored foreground rod keeps ownership but violates final RGB.
        self.edit_art((6, 2), (131, 131, 139, 255))
        self.assertIn("material_rgb", self.check()["counts"])
        # Source contacts and exposed equipment still require exact RGBA.
        self.edit_art((1, 4), (0, 0, 0, 0))
        self.edit_art((6, 6), (0, 0, 0, 0))
        self.assertEqual(self.check()["counts"]["protected_rgba"], 2)

    def test_state_palette_exception_does_not_spend_unused_canonical_slot(self):
        im = Image.open(self.data / "coat-sheet.png").convert("RGBA")
        config = json.loads(self.appearance.read_text())
        for y in range(im.height):
            for x in range(im.width):
                if im.getpixel((x, y)) == (41, 90, 131, 255):
                    im.putpixel((x, y), (49, 90, 131, 255))
                    config["exceptions"].append({"frame": f"step-{x//8}", "point": [x%8, y],
                        "check": "material_rgb", "rgba": [49, 90, 131, 255], "reason": "Approved state lighting"})
        im.save(self.data / "coat-sheet.png")
        component = Image.new("RGB", (10, 1))
        component.putdata([(0, i*255//31, 0) for i in range(1, 11)])
        component.save(self.data / "companion.png")
        config["palette_components"] = {"companion": "companion.png"}
        write_json(self.appearance, config)
        report = self.check()
        self.assertTrue(report["ok"])
        self.assertEqual(report["palette"]["opaque_color_count"], 15)
        self.assertEqual(report["canonical_materials"], {"fabric": "#295a83"})

    def test_layer_order_is_explicit_and_nonbinary_masks_fail(self):
        config = json.loads(self.appearance.read_text())
        mask = Image.new("L", (16, 8))
        mask.putpixel((2, 3), 255)
        mask.save(self.data / "cut.png")
        layer = {"name": "cut", "owner": 1, "mask": "cut.png"}
        config["layers"].append(layer)
        write_json(self.appearance, config)
        self.assertIn("unexpected_silhouette", self.check()["counts"])
        config["layers"] = [layer, *config["layers"][:-1]]
        write_json(self.appearance, config)
        self.assertTrue(self.check()["ok"])
        mask.putpixel((2, 3), 128)
        mask.save(self.data / "cut.png")
        with self.assertRaisesRegex(ValueError, "binary"):
            self.check()

    def test_source_binding_material_coverage_and_asset_paths(self):
        material = Image.open(self.data / "coat-materials.png")
        material.putpixel((2, 3), 0)
        material.save(self.data / "coat-materials.png")
        self.assertEqual(self.check()["counts"]["unresolved_material"], 1)
        config = json.loads(self.appearance.read_text())
        config["material_map"] = "../escape.png"
        write_json(self.appearance, config)
        with self.assertRaisesRegex(ValueError, "escapes"):
            self.check()
        config = json.loads(self.template.read_text())
        config["source_structure_sha256"] = "wrong"
        write_json(self.template, config)
        with self.assertRaisesRegex(ValueError, "does not match"):
            self.check()

    def test_shared_component_palette_counts_separately_rendered_art(self):
        im = Image.new("RGB", (16, 1))
        levels = [i*255//31 for i in range(32)]
        im.putdata([(c, 0, 0) for c in levels[:16]])
        im.save(self.data / "companion.png")
        config = json.loads(self.appearance.read_text())
        config["palette_components"] = {"companion": "companion.png"}
        write_json(self.appearance, config)
        report = self.check()
        self.assertFalse(report["ok"])
        self.assertGreater(report["palette"]["opaque_color_count"], 15)

    def test_review_is_deterministic_source_timed_and_reports_invalid_art(self):
        self.edit_art((2, 3), (49, 90, 131, 255))
        for name in ("one", "two"):
            report = review_bundle(self.source, self.target, self.template, self.appearance, self.root / name,
                                   self.data / "sequences.json", ["step-1"], [self.data / "source.png"])
            self.assertFalse(report["ok"])
            self.assertFalse(report["approved"])
        hashes = lambda path: {p.name: digest(p) for p in path.iterdir()}
        self.assertEqual(hashes(self.root / "one"), hashes(self.root / "two"))
        with Image.open(self.root / "one/comparison-native.png") as im:
            self.assertEqual(im.size, (8, 16))
        with Image.open(self.root / "one/comparison-10x.png") as im:
            self.assertEqual(im.size, (80, 160))
        with Image.open(self.root / "one/animation-west-1x.png") as im:
            self.assertEqual(im.n_frames, 3)  # Repeated stored pose is retained.
            self.assertAlmostEqual(im.info["duration"], 8000/60)
        with Image.open(self.root / "one/animation-east-1x.png") as east, Image.open(self.root / "one/animation-west-1x.png") as west:
            left = west.convert("RGB").crop((0, 0, 8, 8))
            right = east.convert("RGB").crop((0, 0, 8, 8))
            # Checkerboard is fixed; the asymmetric opaque eye is mirrored.
            self.assertEqual(left.getpixel((2, 1)), right.getpixel((5, 1)))
        provenance = json.loads((self.root / "one/provenance.json").read_text())
        for name, sha in provenance["artifacts_sha256"].items():
            self.assertEqual(digest(self.root / "one" / name), sha)
        with self.assertRaisesRegex(ValueError, "already exists"):
            review_bundle(self.source, self.target, self.template, self.appearance, self.root / "one")

    def test_malformed_sequences_publish_nothing(self):
        config = json.loads((self.data / "sequences.json").read_text())
        original = deepcopy(config)
        for bad in ("../unsafe", "missing-frame", "bad-hash"):
            config = deepcopy(original)
            if bad == "../unsafe":
                config["sequences"][bad] = config["sequences"].pop("west")
            elif bad == "missing-frame":
                config["sequences"]["west"]["steps"][0]["frame"] = bad
            else:
                config["source_structure_sha256"] = bad
            write_json(self.data / "sequences.json", config)
            with self.assertRaises(ValueError):
                review_bundle(self.source, self.target, self.template, self.appearance, self.root / "bad", self.data / "sequences.json")
            self.assertFalse((self.root / "bad").exists())

    def test_cli_returns_failure_and_writes_diagnostics_for_invalid_art(self):
        self.edit_art((3, 1), (0, 0, 0, 0))
        result = subprocess.run([sys.executable, "-m", "frametailor", "review", str(self.source), str(self.target),
            "--template", str(self.template), "--appearance", str(self.appearance), "--out", str(self.root / "review")],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn("missing_component_pixel", json.loads(result.stdout)["counts"])
        self.assertTrue((self.root / "review/validation.json").exists())


class PaletteTests(unittest.TestCase):
    def test_full_union_and_component_specific_substitution_is_simultaneous(self):
        colors = [f"#{i*255//31:02x}0000" for i in range(16)]
        report = plan_palette({"fabric": colors[:8], "equipment": colors[8:]})
        self.assertFalse(report["ok"])
        self.assertEqual(report["opaque_color_count"], 16)
        self.assertTrue(report["suggested_single_slot_merges"])
        after = plan_palette({"fabric": colors[:8], "equipment": colors[8:]},
                             {"equipment": {colors[8]: colors[0]}})
        self.assertTrue(after["ok"])
        self.assertEqual(after["opaque_color_count"], 15)
        self.assertFalse(after["approved"])
        shared = plan_palette({"a": [colors[0], colors[1]], "b": [colors[0]]},
                              {"a": {colors[0]: colors[1], colors[1]: colors[2]}})
        self.assertEqual(shared["components"]["a"], sorted([colors[1], colors[2]]))
        self.assertEqual(shared["components"]["b"], [colors[0]])

    def test_exact_bgr555_and_invalid_substitutions(self):
        for value in range(32768):
            rgb = [(value >> s & 31)*255//31 for s in (0, 5, 10)]
            self.assertEqual(bgr555(rgb), value)
        self.assertEqual(plan_palette({"fabric": ["#010000"]})["non_bgr555_colors"], ["#010000"])
        with self.assertRaisesRegex(ValueError, "absent"):
            plan_palette({"fabric": ["#000000"]}, {"fabric": {"#ffffff": "#000000"}})

    def test_frame_major_png_palette_native_roundtrip_and_corruption(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            image = Image.new("P", (32, 16))
            colors = [v for i in range(16) for v in (i*255//31, 0, 0)]
            image.putpalette(colors)
            image.putdata([(x+3*y)%16 for y in range(16) for x in range(32)])
            image.save(root / "indexed.png", bits=4, transparency=0)
            palette = struct.pack("<16H", *(bgr555(colors[i:i+3]) for i in range(0, 48, 3)))
            raw = bytes(image.getpixel((f*16+tx+x, ty+y)) | image.getpixel((f*16+tx+x+1, ty+y)) << 4
                        for f in range(2) for ty in (0, 8) for tx in (0, 8) for y in range(8) for x in range(0, 8, 2))
            (root / "palette.bin").write_bytes(palette)
            (root / "tiles.bin").write_bytes(raw)
            check = lambda: verify_native(root / "indexed.png", root / "palette.bin", root / "tiles.bin", [16, 16])
            self.assertTrue(check()["ok"])
            (root / "tiles.bin").write_bytes(raw[:-1])
            self.assertFalse(check()["ok"])
            (root / "tiles.bin").write_bytes(raw)
            (root / "palette.bin").write_bytes(bytes(32))
            self.assertFalse(check()["ok"])

    def test_exact_apng_fraction_and_no_step_merging(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "animation.png"
            frame = Image.new("RGB", (8, 8), "white")
            timed_apng([frame, frame, frame], [Fraction(8, 60)]*3, False, path)
            data, delays, offset = path.read_bytes(), [], 8
            while offset < len(data):
                size = struct.unpack(">I", data[offset:offset+4])[0]
                if data[offset+4:offset+8] == b"fcTL":
                    delays.append(struct.unpack(">HH", data[offset+28:offset+32]))
                offset += size+12
            self.assertEqual(delays, [(2, 15)]*3)
            with Image.open(path) as im:
                self.assertEqual(im.n_frames, 3)
                self.assertEqual(im.info["loop"], 1)
                for i in range(3):
                    im.seek(i)
                    self.assertEqual(im.convert("RGB").tobytes(), frame.tobytes())
            with self.assertRaisesRegex(ValueError, "16-bit"):
                timed_apng([frame], [Fraction(1, 65536)], True, path)


if __name__ == "__main__":
    unittest.main()
