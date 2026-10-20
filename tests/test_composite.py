# SPDX-License-Identifier: Apache-2.0
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from PIL import Image

from examples.generate_outfit_fixture import create_fixture
from frametailor.composite import composite, masked_replace, preserve_silhouette
from frametailor.pipeline import digest, load, write_json


class MaskedReplaceTests(unittest.TestCase):
    def test_exact_rgba_selection_partial_alpha_erasure_and_expansion(self):
        source = Image.new("RGBA", (5, 1))
        pixels = [(1, 2, 3, 0), (10, 20, 30, 255), (40, 50, 60, 255), (0, 0, 0, 0), (7, 8, 9, 111)]
        source.putdata(pixels)
        candidate = Image.new("RGBA", (5, 1))
        edits = [(90, 80, 70, 255), (90, 80, 70, 37), (9, 8, 7, 0), (90, 80, 70, 255), (1, 1, 1, 0)]
        candidate.putdata(edits)
        mask = Image.new("L", (5, 1))
        mask.putdata([0, 255, 255, 255, 0])
        result = masked_replace(source, candidate, mask)
        self.assertEqual(list(result.get_flattened_data()), [pixels[0], edits[1], edits[2], edits[3], pixels[4]])
        self.assertEqual(list(source.get_flattened_data()), pixels)
        self.assertEqual(list(candidate.get_flattened_data()), edits)

    def test_refuses_soft_masks_dimensions_and_implicit_mode_conversion(self):
        source = Image.new("RGBA", (2, 1))
        for candidate, mask in [(source, Image.new("L", (2, 1), 128)),
                                (source, Image.new("L", (1, 1))),
                                (source.convert("RGB"), Image.new("L", (2, 1)))]:
            with self.assertRaises(ValueError):
                masked_replace(source, candidate, mask)

    def test_silhouette_preserves_alpha_protection_and_hidden_source_rgb(self):
        source = Image.new("RGBA", (6, 1))
        pixels = [(1, 2, 3, 0), (10, 20, 30, 255), (40, 50, 60, 91),
                  (8, 9, 10, 255), (7, 8, 9, 111), (2, 3, 4, 255)]
        source.putdata(pixels)
        candidate = Image.new("RGBA", source.size, (90, 80, 70, 37))
        candidate.putpixel((3, 0), (250, 251, 252, 0))
        candidate.putpixel((5, 0), (90, 80, 70, 255))
        mask = Image.new("L", source.size)
        mask.putdata([255, 255, 255, 255, 0, 255])
        with self.assertRaisesRegex(ValueError, "1 missing/transparent"):
            preserve_silhouette(source, candidate, mask)
        result, fallback = preserve_silhouette(source, candidate, mask, "source")
        self.assertEqual(fallback, [[3, 0]])
        self.assertEqual(list(result.get_flattened_data()),
                         [pixels[0], (90, 80, 70, 255), (90, 80, 70, 91), pixels[3], pixels[4], (90, 80, 70, 255)])
        self.assertEqual(list(source.get_flattened_data()), pixels)
        with self.assertRaises(ValueError):
            preserve_silhouette(source, candidate, Image.new("L", source.size, 128))
        with self.assertRaises(ValueError):
            preserve_silhouette(source, candidate, mask, "invent")


class CompositeBundleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        create_fixture(self.source)
        self.manifest = self.source / "animation.json"
        self.mask = self.source / "editable-mask.png"
        self.spec, self.original, _ = load(self.manifest)
        self.candidate = self.root / "candidate"
        self.candidate.mkdir()
        write_json(self.candidate / "animation.json", self.spec)
        self.raw = self.original.copy()
        for i in range(4):
            self.raw.putpixel((i * 64 + 32, 40), (100, 70, 140, 253))  # authorized expansion
            self.raw.putpixel((i * 64 + 28, 15), (255, 255, 255, 0))  # forbidden head edit
        self.raw.save(self.candidate / "sheet.png")
        self.out = self.root / "result"

    def run_composite(self, out=None):
        return composite(self.manifest, self.candidate / "animation.json", self.mask, out or self.out)

    def test_counts_protection_metadata_raw_preservation_and_preview(self):
        original_hash = digest(self.manifest)
        raw_hash = digest(self.candidate / "sheet.png")
        report = self.run_composite()
        self.assertTrue(report["ok"])
        self.assertFalse(report["approved"])
        self.assertEqual(report["transferred_changed_pixels"], 4)
        self.assertEqual(report["discarded_unauthorized_changes"], 4)
        self.assertEqual(report["remaining_unauthorized_changes"], 0)
        self.assertEqual(digest(self.candidate / "sheet.png"), raw_hash)
        self.assertEqual(digest(self.out / "inputs/candidate.png"), raw_hash)
        self.assertEqual(digest(self.manifest), original_hash)
        spec, sheet, frames = load(self.out / "animation.json")
        self.assertEqual(spec, self.spec)
        with Image.open(self.mask) as mask:
            for a, b, p, m in zip(self.original.get_flattened_data(), self.raw.get_flattened_data(), sheet.get_flattened_data(), mask.get_flattened_data()):
                self.assertEqual(p, b if m else a)
        checks = json.loads((self.out / "validation.json").read_text())
        self.assertFalse(checks["raw_outfit"]["ok"])
        self.assertTrue(checks["composite_outfit"]["ok"])
        self.assertFalse(checks["composite_exact_alpha"]["ok"])
        with Image.open(self.out / "preview.png") as image:
            self.assertEqual(image.n_frames, 4)
            for i, entry in enumerate(spec["frames"]):
                image.seek(i)
                self.assertEqual(image.info["duration"], entry["duration_ms"])
                self.assertEqual(image.convert("RGBA").tobytes(), frames[i].tobytes())

    def test_byte_reproducible_bundle_and_maps(self):
        self.run_composite()
        other = self.root / "second"
        self.run_composite(other)
        files = lambda root: {p.relative_to(root): digest(p) for p in root.rglob("*") if p.is_file()}
        self.assertEqual(files(self.out), files(other))
        with Image.open(self.out / "transferred-changes.png") as image:
            self.assertEqual(sum(v == 255 for v in image.get_flattened_data()), 4)
        with Image.open(self.out / "discarded-changes.png") as image:
            self.assertEqual(sum(v == 255 for v in image.get_flattened_data()), 4)
        provenance = json.loads((self.out / "provenance.json").read_text())
        for p, h in provenance["artifacts_sha256"].items():
            self.assertEqual(digest(self.out / p), h)

    def test_metadata_mismatch_and_overwrite_fail_without_repair(self):
        for mutate in [lambda s: s["frames"].reverse(),
                       lambda s: s["frames"][0].update(anchor=[1, 1]),
                       lambda s: s["frames"][0].update(duration_ms=999)]:
            spec = deepcopy(self.spec)
            mutate(spec)
            write_json(self.candidate / "animation.json", spec)
            with self.assertRaisesRegex(ValueError, "metadata"):
                self.run_composite()
            self.assertFalse(self.out.exists())
        write_json(self.candidate / "animation.json", self.spec)
        self.run_composite()
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.run_composite()

    def test_padding_is_protected_and_counted(self):
        # Leave an undeclared sheet cell and mask it black.
        spec = deepcopy(self.spec)
        spec["frames"].pop()
        write_json(self.manifest, spec)
        write_json(self.candidate / "animation.json", spec)
        with Image.open(self.mask) as mask:
            reduced = mask.copy()
        reduced.paste(0, (192, 0, 256, 64))
        reduced.save(self.mask)
        report = self.run_composite()
        self.assertEqual(report["discarded_padding_changes"], 2)
        self.assertEqual(report["discarded_unauthorized_changes"], 5)
        _, result, _ = load(self.out / "animation.json")
        self.assertEqual(result.crop((192, 0, 256, 64)).tobytes(), self.original.crop((192, 0, 256, 64)).tobytes())

    def test_unchanged_candidate_is_not_approved_and_cli_exits_nonzero(self):
        self.original.save(self.candidate / "sheet.png")
        args = [sys.executable, "-m", "frametailor", "composite", str(self.manifest),
                str(self.candidate / "animation.json"), "--editable-mask", str(self.mask), "--out", str(self.out)]
        process = subprocess.run(args, capture_output=True, text=True)
        self.assertEqual(process.returncode, 1)
        report = json.loads(process.stdout)
        self.assertFalse(report["ok"])
        self.assertFalse(report["approved"])
        self.assertTrue((self.out / "validation.json").exists())

    def test_silhouette_cli_fallback_locations_maps_checks_and_reproducibility(self):
        with Image.open(self.mask) as image:
            mask = image.copy()
        points = []
        for i in range(4):
            points.append(next((x, y) for y in range(64) for x in range(i * 64, (i + 1) * 64)
                               if mask.getpixel((x, y)) and self.original.getpixel((x, y))[3]))
        for i, point in enumerate(points):
            self.raw.putpixel(point, (60, 90, 130, 0 if i == 2 else 72))
        second = next((x, y) for y in range(64) for x in range(128, 192)
                      if (x, y) != points[2] and mask.getpixel((x, y)) and self.original.getpixel((x, y))[3])
        self.raw.putpixel(second, (60, 90, 130, 255))
        self.raw.save(self.candidate / "sheet.png")
        args = [sys.executable, "-m", "frametailor", "composite", str(self.manifest),
                str(self.candidate / "animation.json"), "--editable-mask", str(self.mask),
                "--out", str(self.out), "--mode", "preserve-source-silhouette"]
        refused = subprocess.run(args, capture_output=True, text=True)
        self.assertEqual(refused.returncode, 1)
        self.assertFalse(self.out.exists())
        accepted = subprocess.run(args + ["--missing-candidate", "source"], capture_output=True, text=True)
        self.assertEqual(accepted.returncode, 0, accepted.stdout)
        report = json.loads(accepted.stdout)
        self.assertFalse(report["approved"])
        self.assertEqual(report["source_colour_fallback_locations"], [list(points[2])])
        self.assertEqual(report["source_colour_fallback_count"], 1)
        self.assertEqual(report["frames"][2]["source_colour_fallback_locations"], [[points[2][0] - 128, points[2][1]]])
        self.assertEqual(report["source_alpha_changes"], 0)
        self.assertEqual(report["authorized_alpha_changes"], 0)
        _, result, _ = load(self.out / "animation.json")
        self.assertEqual(result.getchannel("A").tobytes(), self.original.getchannel("A").tobytes())
        for a, b, allowed in zip(self.original.get_flattened_data(), result.get_flattened_data(), mask.get_flattened_data()):
            if not allowed or not a[3]:
                self.assertEqual(a, b)
        with Image.open(self.out / "source-colour-fallback.png") as image:
            self.assertEqual(sum(bool(v) for v in image.get_flattened_data()), 1)
            self.assertEqual(image.getpixel(points[2]), 255)
        checks = json.loads((self.out / "validation.json").read_text())
        self.assertTrue(checks["composite_exact_alpha"]["ok"])
        other = self.root / "repeat"
        composite(self.manifest, self.candidate / "animation.json", self.mask, other,
                  "preserve-source-silhouette", "source")
        files = lambda root: {p.relative_to(root): digest(p) for p in root.rglob("*") if p.is_file()}
        self.assertEqual(files(self.out), files(other))

    def test_invalid_mode_and_fallback_combinations_rejected(self):
        for mode, policy in [("repair", "error"), ("rgba-replace", "source"),
                             ("preserve-source-silhouette", "invent")]:
            with self.assertRaises(ValueError):
                composite(self.manifest, self.candidate / "animation.json", self.mask, self.out, mode, policy)
            self.assertFalse(self.out.exists())


if __name__ == "__main__":
    unittest.main()
