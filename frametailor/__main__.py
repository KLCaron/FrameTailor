# SPDX-License-Identifier: Apache-2.0
import argparse
import json
from pathlib import Path

from .benchmark import validate_outfit
from .composite import composite
from .demo import create_demo
from .pipeline import run, validate
from .native import native_outfit
from .ownership import validate_ownership
from .palette import plan_palette, verify_native
from .review import review_bundle


def main() -> int:
    parser = argparse.ArgumentParser(description="Offline, structure-preserving sprite transformations")
    commands = parser.add_subparsers(dest="command", required=True)
    demo = commands.add_parser("demo", help="create original four-frame test sprites")
    demo.add_argument("--out", required=True, type=Path)
    transform = commands.add_parser("transform", help="apply a deterministic palette mapping")
    transform.add_argument("source", type=Path, help="animation manifest JSON")
    transform.add_argument("--config", required=True, type=Path)
    transform.add_argument("--out", required=True, type=Path)
    native = commands.add_parser("outfit", help="apply source-bound semantic regions and native pixel details")
    native.add_argument("source", type=Path)
    native.add_argument("--template", required=True, type=Path)
    native.add_argument("--outfit", required=True, type=Path)
    native.add_argument("--out", required=True, type=Path)
    merge = commands.add_parser("composite", help="composite only within source-authorized edit regions")
    merge.add_argument("source", type=Path)
    merge.add_argument("candidate", type=Path)
    merge.add_argument("--editable-mask", required=True, type=Path)
    merge.add_argument("--out", required=True, type=Path)
    merge.add_argument("--mode", choices=["rgba-replace", "preserve-source-silhouette"], default="rgba-replace")
    merge.add_argument("--missing-candidate", choices=["error", "source"], default="error",
                       help="transparent candidate policy in silhouette mode; source fallback is recorded")
    check = commands.add_parser("validate", help="compare candidate structure against its source")
    check.add_argument("source", type=Path)
    check.add_argument("candidate", type=Path)
    check.add_argument("--profile", choices=["exact-alpha-v1", "outfit-regions-v1"], default="exact-alpha-v1")
    check.add_argument("--editable-mask", type=Path, help="source-authored binary PNG; required for outfit profile")
    for name in ("ownership", "review"):
        command = commands.add_parser(name, help="check explicit ownership, materials and palette constraints" if name == "ownership"
                                      else "write native/10x review sheets, overlays and coordinate ledgers")
        command.add_argument("source", type=Path)
        command.add_argument("candidate", type=Path)
        command.add_argument("--template", required=True, type=Path)
        command.add_argument("--appearance", required=True, type=Path)
        if name == "review":
            command.add_argument("--out", required=True, type=Path)
            command.add_argument("--sequences", type=Path, help="source-bound tick timing and explicit flips")
            command.add_argument("--frames", nargs="+", help="representative frame ids for comparisons; checks still cover all frames")
            command.add_argument("--reference", action="append", type=Path, help="private concept/reference image; repeatable")
    palette = commands.add_parser("palette-plan", help="check the full opaque component-color union before authoring")
    palette.add_argument("config", type=Path, help="JSON with components and optional per-component substitutions")
    native_check = commands.add_parser("verify-native", help="verify existing indexed PNG/BGR555/frame-major 4bpp representations")
    native_check.add_argument("indexed", type=Path)
    native_check.add_argument("--palette", required=True, type=Path)
    native_check.add_argument("--native", required=True, type=Path)
    native_check.add_argument("--frame-size", required=True, type=int, nargs=2, metavar=("WIDTH", "HEIGHT"))
    args = parser.parse_args()
    try:
        if args.command == "demo":
            create_demo(args.out)
            result = {"ok": True, "manifest": str(args.out / "animation.json")}
        elif args.command == "transform":
            result = run(args.source, args.config, args.out)
        elif args.command == "composite":
            result = composite(args.source, args.candidate, args.editable_mask, args.out, args.mode, args.missing_candidate)
        elif args.command == "outfit":
            result = native_outfit(args.source, args.template, args.outfit, args.out)
        elif args.command == "ownership":
            result = validate_ownership(args.source, args.candidate, args.template, args.appearance)
        elif args.command == "review":
            result = review_bundle(args.source, args.candidate, args.template, args.appearance, args.out,
                                   args.sequences, args.frames, args.reference)
        elif args.command == "palette-plan":
            config = json.loads(args.config.read_text())
            result = plan_palette(config["components"], config.get("substitutions"))
        elif args.command == "verify-native":
            result = verify_native(args.indexed, args.palette, args.native, args.frame_size)
        else:
            if args.profile == "outfit-regions-v1":
                if args.editable_mask is None:
                    raise ValueError("outfit-regions-v1 requires --editable-mask")
                result = validate_outfit(args.source, args.candidate, args.editable_mask)
            else:
                if args.editable_mask is not None:
                    raise ValueError("--editable-mask requires outfit-regions-v1")
                result = validate(args.source, args.candidate)
    except (ValueError, OSError, KeyError, TypeError) as error:
        print(json.dumps({"ok": False, "errors": [str(error)]}))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
