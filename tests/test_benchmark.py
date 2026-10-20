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
from frametailor.benchmark import validate_outfit
from frametailor.pipeline import load, validate, write_json


class OutfitBenchmarkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        create_fixture(self.source)
        self.manifest = self.source / "animation.json"
        self.mask = self.source / "editable-mask.png"
        self.candidate = self.root / "candidate"
        self.candidate.mkdir()
        self.spec, self.sheet, self.frames = load(self.manifest)
        write_json(self.candidate / "animation.json", self.spec)
        # A synthetic, permitted silhouette edit, deliberately not a real outfit.
        self.changed = self.sheet.copy()
        for i in range(4):
            self.changed.putpixel((i * 64 + 32, 40), (110, 70, 150, 255))
        self.changed.save(self.candidate / "sheet.png")

    def check(self):
        return validate_outfit(self.manifest, self.candidate / "animation.json", self.mask)

    def test_permitted_silhouette_change_never_approves(self):
        self.assertFalse(validate(self.manifest, self.candidate / "animation.json")["ok"])
        result = self.check()
        self.assertTrue(result["ok"])
        self.assertFalse(result["approved"])
        self.assertEqual(result["review_status"], "pending")
        self.assertIn("genuine garment change", result["review_required"])

    def test_protected_hand_pose_and_identity_changes_rejected(self):
        for point in [(17, 38), (37, 19), (24, 57)]:
            sheet = self.changed.copy()
            sheet.putpixel(point, (255, 255, 255, 255))
            sheet.save(self.candidate / "sheet.png")
            self.assertFalse(self.check()["ok"])

    def test_unchanged_and_cross_frame_source_substitution_rejected(self):
        for index in (0, 2):
            sheet = self.changed.copy()
            sheet.paste(self.frames[index], (0, 0))
            sheet.save(self.candidate / "sheet.png")
            self.assertTrue(any("source frame" in e for e in self.check()["errors"]))

    def test_duplicate_candidate_rejected(self):
        self.changed.paste(self.changed.crop((0, 0, 64, 64)), (128, 0))
        self.changed.save(self.candidate / "sheet.png")
        self.assertTrue(any("duplicate" in e for e in self.check()["errors"]))

    def test_metadata_changes_rejected(self):
        mutations = [lambda s: s["frames"].reverse(),
                     lambda s: s["frames"].pop(),
                     lambda s: s["frames"][0].update(id="replacement"),
                     lambda s: s["frames"][0].update(duration_ms=3),
                     lambda s: s["frames"][0].update(anchor=[31, 58])]
        for mutate in mutations:
            spec = deepcopy(self.spec)
            mutate(spec)
            write_json(self.candidate / "animation.json", spec)
            self.assertFalse(self.check()["ok"])

    def test_bad_masks_rejected(self):
        for size, value in [((2, 2), 255), ((256, 64), 128), ((256, 64), 255), ((256, 64), 0)]:
            Image.new("L", size, value).save(self.mask)
            with self.assertRaises(ValueError):
                self.check()

    def test_fixture_reproducible_asymmetric_and_reference_is_not_recolor(self):
        other = self.root / "other"
        create_fixture(other)
        files = lambda root: {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
        self.assertEqual(files(self.source), files(other))
        self.assertEqual(len({f.tobytes() for f in self.frames}), 4)
        for frame in self.frames:
            self.assertNotEqual(frame.tobytes(), frame.transpose(Image.Transpose.FLIP_LEFT_RIGHT).tobytes())
        with Image.open(self.source / "outfit-reference.png") as reference, Image.open(self.mask) as mask:
            a, b = self.frames[0], reference.convert("RGBA")
            self.assertNotEqual(a.getchannel("A").tobytes(), b.getchannel("A").tobytes())
            for p, q, allowed in zip(a.get_flattened_data(), b.get_flattened_data(), mask.crop((0, 0, 64, 64)).get_flattened_data()):
                if not allowed:
                    self.assertEqual(p, q)
        # Checked-in examples must remain consistent with the generator.
        self.assertEqual(files(self.source), files(Path(__file__).resolve().parents[1] / "examples/outfit"))

    def test_cli_requires_explicit_mask_and_keeps_default_profile(self):
        base = [sys.executable, "-m", "frametailor", "validate", str(self.manifest), str(self.candidate / "animation.json")]
        default = subprocess.run(base, capture_output=True, text=True)
        self.assertEqual(json.loads(default.stdout)["profile"], "exact-alpha-v1")
        missing = subprocess.run(base + ["--profile", "outfit-regions-v1"], capture_output=True, text=True)
        self.assertEqual(missing.returncode, 1)
        valid = subprocess.run(base + ["--profile", "outfit-regions-v1", "--editable-mask", str(self.mask)], capture_output=True, text=True)
        self.assertEqual(valid.returncode, 0)
        self.assertFalse(json.loads(valid.stdout)["approved"])


if __name__ == "__main__":
    unittest.main()
