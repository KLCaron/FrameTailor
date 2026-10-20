# SPDX-License-Identifier: Apache-2.0
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from PIL import Image

from frametailor.demo import create_demo
from frametailor.pipeline import digest, load, palette_transform, run, validate, write_json


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        create_demo(self.source)
        self.manifest = self.source / "animation.json"
        self.config = self.source / "transform.json"
        self.out = self.root / "result"

    def bake(self):
        return run(self.manifest, self.config, self.out)

    def edit_candidate(self, change):
        path = self.out / "animation.json"
        spec = json.loads(path.read_text())
        change(spec)
        write_json(path, spec)

    def test_end_to_end_pixels_metadata_and_preview(self):
        result = self.bake()
        self.assertTrue(result["ok"])
        self.assertEqual(result["changed_pixels"], 552)
        before, a, source_frames = load(self.manifest)
        after, b, target_frames = load(self.out / "animation.json")
        self.assertEqual(before, after)
        colors = {(59, 130, 246): (245, 158, 11), (29, 78, 216): (180, 83, 9)}
        for p, q in zip(a.get_flattened_data(), b.get_flattened_data()):
            self.assertEqual(q, (*colors.get(p[:3], p[:3]), p[3]) if p[3] else p)
        for i, frame in enumerate(target_frames):
            with Image.open(self.out / "frames" / f"{i:04d}.png") as exported:
                self.assertEqual(exported.convert("RGBA").tobytes(), frame.tobytes())
            self.assertEqual(source_frames[i].getchannel("A").tobytes(), frame.getchannel("A").tobytes())
        with Image.open(self.out / "preview.png") as preview:
            self.assertEqual(preview.n_frames, 4)
            self.assertEqual(preview.info["loop"], 0)
            for i, entry in enumerate(after["frames"]):
                preview.seek(i)
                self.assertEqual(preview.info["duration"], entry["duration_ms"])
                self.assertEqual(preview.convert("RGBA").tobytes(), target_frames[i].tobytes())

    def test_reproducibility_and_provenance(self):
        self.bake()
        other = self.root / "other"
        run(self.manifest, self.config, other)
        files = lambda root: {p.relative_to(root): digest(p) for p in root.rglob("*") if p.is_file()}
        self.assertEqual(files(self.out), files(other))
        provenance = json.loads((self.out / "provenance.json").read_text())
        for name, expected in provenance["artifacts_sha256"].items():
            self.assertEqual(digest(self.out / name), expected)
        self.assertEqual(provenance["source_manifest_sha256"], digest(self.manifest))
        self.assertTrue(validate(self.out / "inputs/animation.json", self.out / "animation.json")["ok"])

    def test_detects_timing_anchor_order_count_loop_and_extra_metadata_changes(self):
        self.bake()
        original = json.loads((self.out / "animation.json").read_text())
        mutations = [lambda s: s["frames"][0].update(duration_ms=999),
                     lambda s: s["frames"][0].update(anchor=[0, 0]),
                     lambda s: s["frames"].reverse(),
                     lambda s: s["frames"].pop(),
                     lambda s: s.update(loop=False),
                     lambda s: s.update(animation="other")]
        for change in mutations:
            spec = deepcopy(original)
            change(spec)
            write_json(self.out / "animation.json", spec)
            self.assertFalse(validate(self.manifest, self.out / "animation.json")["ok"])

    def test_detects_pixel_shift_without_changing_canvas(self):
        self.bake()
        path = self.out / "sheet.png"
        with Image.open(path) as im:
            shifted = Image.new("RGBA", im.size)
            shifted.paste(im, (1, 0))
        shifted.save(path)
        report = validate(self.manifest, self.out / "animation.json")
        self.assertFalse(report["ok"])
        self.assertTrue(any("alpha mask" in error for error in report["errors"]))

    def test_rejects_invalid_input_before_writing_output(self):
        original = json.loads(self.manifest.read_text())
        mutations = [lambda s: s["frames"][0].update(rect=[90, 0, 24, 24]),
                     lambda s: s["frames"][0].update(duration_ms=0),
                     lambda s: s["frames"][0].update(duration_ms=True),
                     lambda s: s["frames"][1].update(id="step-0"),
                     lambda s: s["frames"][1].update(rect=[0, 0, 24, 24]),
                     lambda s: s.update(sheet="../escape.png"),
                     lambda s: s.update(frames=[])]
        for change in mutations:
            spec = deepcopy(original)
            change(spec)
            write_json(self.manifest, spec)
            with self.assertRaises(ValueError):
                self.bake()
            self.assertFalse(self.out.exists())

    def test_alpha_and_unmapped_colors_and_simultaneous_lookup(self):
        write_json(self.config, {"type": "palette", "colors": {"#010203": "#040506", "#040506": "#070809"}})
        transform, _ = palette_transform(self.config)
        image = Image.new("RGBA", (4, 1))
        pixels = [(1, 2, 3, 0), (1, 2, 3, 37), (4, 5, 6, 255), (9, 9, 9, 255)]
        image.putdata(pixels)
        self.assertEqual(list(transform(image).get_flattened_data()),
                         [pixels[0], (4, 5, 6, 37), (7, 8, 9, 255), pixels[3]])

    def test_refuses_noop_and_overwrite(self):
        write_json(self.config, {"type": "palette", "colors": {"#010203": "#040506"}})
        with self.assertRaisesRegex(ValueError, "no visible change"):
            self.bake()
        self.assertFalse(self.out.exists())
        self.out.mkdir()
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.bake()

    def test_sheet_gaps_and_unequal_frame_sizes_preserved(self):
        spec = json.loads(self.manifest.read_text())
        spec["frames"] = [spec["frames"][0], spec["frames"][2]]
        spec["frames"][1]["rect"][2] = 23
        write_json(self.manifest, spec)
        self.bake()
        self.assertTrue(validate(self.manifest, self.out / "animation.json")["ok"])
        self.assertFalse((self.out / "preview.png").exists())
        with Image.open(self.source / "sheet.png") as source, Image.open(self.out / "sheet.png") as target:
            self.assertEqual(source.crop((24, 0, 48, 24)).tobytes(), target.crop((24, 0, 48, 24)).tobytes())
        with Image.open(self.out / "sheet.png") as target:
            corrupted = target.copy()
        corrupted.putpixel((30, 0), (1, 2, 3, 255))
        corrupted.save(self.out / "sheet.png")
        self.assertFalse(validate(self.manifest, self.out / "animation.json")["ok"])

    def test_cli_failure_returns_json_and_nonzero(self):
        self.bake()
        self.edit_candidate(lambda s: s.update(loop=False))
        result = subprocess.run([sys.executable, "-m", "frametailor", "validate", str(self.manifest),
                                 str(self.out / "animation.json")], capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertFalse(json.loads(result.stdout)["ok"])


if __name__ == "__main__":
    unittest.main()
