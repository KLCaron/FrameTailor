# SPDX-License-Identifier: Apache-2.0
"""Private review bundles with explicit sequence timing, native pixels and ledgers."""
from fractions import Fraction
from io import BytesIO
import csv
import json
from pathlib import Path
import re
import struct
import tempfile
import zlib

from PIL import Image, ImageOps

from .native import structure_digest
from .ownership import inspect_ownership
from .pipeline import digest, load, write_json


def _chunk(kind, data):
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind+data) & 0xffffffff)


def timed_apng(frames, delays, loop, path):
    """Encode full RGB frames with exact rational seconds, retaining repeated steps."""
    if (not frames or len(frames) != len(delays) or len({f.size for f in frames}) != 1
            or type(loop) is not bool):
        raise ValueError("APNG needs equal-size frames, explicit delays and loop")
    w, h = frames[0].size
    data = bytearray(b"\x89PNG\r\n\x1a\n")
    data.extend(_chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)))
    data.extend(_chunk(b"acTL", struct.pack(">II", len(frames), 0 if loop else 1)))
    sequence = 0
    for index, (frame, delay) in enumerate(zip(frames, delays)):
        delay = Fraction(delay)
        if delay <= 0 or delay.numerator > 65535 or delay.denominator > 65535:
            raise ValueError("APNG delay must be exactly representable as positive 16-bit rational seconds")
        data.extend(_chunk(b"fcTL", struct.pack(">IIIIIHHBB", sequence, w, h, 0, 0,
                          delay.numerator, delay.denominator, 0, 0)))
        sequence += 1
        buf = BytesIO()
        frame.convert("RGB").save(buf, format="PNG")
        png, offset = buf.getvalue(), 8
        while offset < len(png):
            size = struct.unpack(">I", png[offset:offset+4])[0]
            kind, payload = png[offset+4:offset+8], png[offset+8:offset+8+size]
            if kind == b"IDAT":
                if index == 0:
                    data.extend(_chunk(b"IDAT", payload))
                else:
                    data.extend(_chunk(b"fdAT", struct.pack(">I", sequence)+payload))
                    sequence += 1
            offset += size+12
    data.extend(_chunk(b"IEND", b""))
    path.write_bytes(data)


def _board(collections):
    w, h = max(f.width for row in collections for f in row), max(f.height for row in collections for f in row)
    board = Image.new("RGB", (w*max(map(len, collections)), h*len(collections)), "#aeb4bc")
    for y in range(board.height):
        for x in range(board.width):
            if (x//4+y//4) % 2:
                board.putpixel((x, y), (219, 222, 227))
    for row, frames in enumerate(collections):
        for col, frame in enumerate(frames):
            frame = frame.convert("RGBA")
            board.paste(frame, (col*w, row*h), frame)
    return board


def _save_scales(image, root, name):
    image.save(root / f"{name}-native.png")
    image.resize((image.width*10, image.height*10), Image.Resampling.NEAREST).save(root / f"{name}-10x.png")


def review_bundle(source: Path, candidate: Path, template: Path, appearance: Path, out: Path,
                  sequences: Path | None = None, frame_ids: list[str] | None = None,
                  references: list[Path] | None = None) -> dict:
    """Publish diagnostics even for invalid artwork; fail malformed contracts atomically.

    Sequence JSON binds source_structure_sha256 and declares named sequences of
    frame ids, ticks, flip_x/flip_y and tick_hz. Engine command interpretation and
    finite-loop expansion belong in the caller's retained source contract.
    """
    if out.exists():
        raise ValueError("output directory already exists; choose a fresh path")
    report, source_map, final_map, materials = inspect_ownership(source, candidate, template, appearance)
    spec, before, original = load(source)
    _, after, changed = load(candidate)
    entries = {e["id"]: e for e in spec["frames"]}
    selected = list(entries) if frame_ids is None else frame_ids
    if (not selected or len(set(selected)) != len(selected) or not set(selected) <= set(entries)):
        raise ValueError("review frames must be unique known frame ids")
    indexes = {e["id"]: i for i, e in enumerate(spec["frames"])}
    sequence_spec = None
    if sequences is not None:
        sequence_spec = json.loads(sequences.read_text())
        if (not isinstance(sequence_spec, dict)
                or sequence_spec.get("source_structure_sha256") != structure_digest(spec)
                or type(sequence_spec.get("tick_hz")) is not int or sequence_spec["tick_hz"] <= 0
                or not isinstance(sequence_spec.get("sequences"), dict) or not sequence_spec["sequences"]):
            raise ValueError("sequences need a source structure hash, positive tick_hz and named sequences")
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".frametailor-review-", dir=out.parent) as staging:
        root = Path(staging) / "bundle"
        root.mkdir()
        _save_scales(_board([[row[indexes[n]] for n in selected] for row in (original, changed)]), root, "comparison")
        source_map.save(root / "source-ownership.png")
        final_map.save(root / "final-ownership.png")
        materials.save(root / "materials.png")
        overlays = [Image.new("RGBA", after.size), Image.new("RGBA", after.size)]
        silhouette = Image.new("RGBA", after.size)
        for y in range(after.height):
            for x in range(after.width):
                for overlay, image, labels in zip(overlays, (before, after), (source_map, final_map)):
                    owner = labels.getpixel((x, y))
                    kind = report["components"].get(str(owner), {}).get("kind")
                    if kind != "exterior":
                        # Stable tint over actual art, independent of palette classification.
                        tint = ((owner*67)%192+63, (owner*113)%192+63, (owner*157)%192+63)
                        pixel = image.getpixel((x, y))
                        color = tuple((c+t)//2 for c, t in zip(pixel[:3], tint)) if pixel[3] else tint
                        overlay.putpixel((x, y), (*color, 255))
                a, b = before.getpixel((x, y))[3], after.getpixel((x, y))[3]
                silhouette.putpixel((x, y), (255, 60, 150, 255) if a and not b else
                                    (0, 190, 220, 255) if b and not a else
                                    (40, 40, 40, 255) if b else (0, 0, 0, 0))
        for name, images in (("ownership", overlays), ("silhouette", [silhouette])):
            rows = [[image.crop((e["rect"][0], e["rect"][1], e["rect"][0]+e["rect"][2],
                     e["rect"][1]+e["rect"][3])) for n in selected for e in [entries[n]]] for image in images]
            _save_scales(_board(rows), root, name)
        ledger = []
        for entry in spec["frames"]:
            x, y, w, h = entry["rect"]
            for py in range(h):
                for px in range(w):
                    p = (x+px, y+py)
                    a, b = before.getpixel(p), after.getpixel(p)
                    if a != b:
                        ledger.append({"frame": entry["id"], "x": px, "y": py, "sheet_x": p[0], "sheet_y": p[1],
                            "source_rgba": list(a), "candidate_rgba": list(b),
                            "source_owner": source_map.getpixel(p), "final_owner": final_map.getpixel(p)})
        write_json(root / "correction-ledger.json", ledger)
        with (root / "correction-ledger.csv").open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=["frame", "x", "y", "sheet_x", "sheet_y", "source_rgba",
                                                       "candidate_rgba", "source_owner", "final_owner"])
            writer.writeheader()
            writer.writerows(ledger)
        write_json(root / "validation.json", report)
        write_json(root / "colour-fidelity.json", {"counts": report["counts"], "palette": report["palette"],
            "canonical_materials": report["canonical_materials"],
            "mismatches": [d for d in report["diagnostics"] if d["check"] == "material_rgb"],
            "exceptions": [d for d in report["applied_exceptions"] if d["check"] == "material_rgb"], "approved": False})
        layout = {"frames": selected, "rows": ["source", "candidate"], "components": report["components"],
                  "ownership_tints": {label: [(int(label)*n)%192+63 for n in (67, 113, 157)]
                                      for label in report["components"]},
                  "silhouette_legend": {"pink": "removed", "cyan": "added", "dark": "retained"},
                  "sequences": sequence_spec, "references": [], "approved": False}
        if sequence_spec is not None:
            for name, sequence in sequence_spec["sequences"].items():
                if (not isinstance(name, str) or re.fullmatch(r"[A-Za-z0-9_-]+", name) is None
                        or not isinstance(sequence, dict) or type(sequence.get("loop")) is not bool
                        or not isinstance(sequence.get("steps"), list) or not sequence["steps"]):
                    raise ValueError("sequence needs a safe name, explicit loop and nonempty steps")
                panels, delays = [], []
                directions = [[], []]
                for step in sequence["steps"]:
                    if (not isinstance(step, dict) or step.get("frame") not in entries
                            or type(step.get("ticks")) is not int or step["ticks"] <= 0
                            or type(step.get("flip_x", False)) is not bool or type(step.get("flip_y", False)) is not bool):
                        raise ValueError("sequence step needs known frame, positive ticks and boolean flips")
                    pair = []
                    for row, frames in enumerate((original, changed)):
                        frame = frames[indexes[step["frame"]]]
                        if step.get("flip_x", False):
                            frame = ImageOps.mirror(frame)
                        if step.get("flip_y", False):
                            frame = ImageOps.flip(frame)
                        pair.append(frame)
                        directions[row].append(frame)
                    panels.append(_board([pair]))
                    delays.append(Fraction(step["ticks"], sequence_spec["tick_hz"]))
                _save_scales(_board(directions), root, f"sequence-{name}")
                for scale in (1, 10):
                    frames = [p.resize((p.width*scale, p.height*scale), Image.Resampling.NEAREST) for p in panels]
                    timed_apng(frames, delays, sequence["loop"], root / f"animation-{name}-{scale}x.png")
        for i, path in enumerate(references or []):
            with Image.open(path) as image:
                image.convert("RGBA").save(root / f"reference-{i}.png")
            layout["references"].append({"file": f"reference-{i}.png", "input_sha256": digest(path),
                "comparison": "human concept reference; no inferred pose or pixel alignment"})
        write_json(root / "layout.json", layout)
        write_json(root / "provenance.json", {"inputs_sha256": {**report["inputs_sha256"],
            **({"sequences": digest(sequences)} if sequences else {})},
            "implementation_sha256": report["implementation_sha256"],
            "artifacts_sha256": {p.name: digest(p) for p in sorted(root.iterdir())}, "approved": False})
        root.rename(out)
    return report
