# FrameTailor

An offline, engine-independent CLI for transforming existing sprite sheets while preserving animation metadata. It supports exact palette swaps and binary-mask compositing of externally generated candidates; AI generation is not implemented. Python 3.11+ and Pillow are the only runtime requirements.

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -e .
frametailor transform examples/robot/animation.json \
  --config examples/robot/transform.json --out output/robot
frametailor validate examples/robot/animation.json output/robot/animation.json
python -m unittest discover -s tests -v
```

Use a fresh output directory. `python -m frametailor` also works from the repository with Pillow installed.

![Source frames above their recolored variants](examples/robot-comparison.png)

Input is a static PNG sheet and a [JSON manifest](examples/robot/animation.json) declaring frame ids, rectangles, durations, anchors and looping. Frames may use any nonoverlapping layout. [Palette mappings](examples/robot/transform.json) apply simultaneously, preserving alpha and unmapped colors.

Output includes the transformed sheet, frame PNGs, unchanged animation metadata, an APNG preview, source/output comparison, validation report, portable inputs and SHA-256 provenance. Identical inputs and runtime versions produce identical bundles. The manifest and individual PNGs are authoritative: APNG can merge adjacent identical frames, and previews are omitted for unequal frame sizes. Loose-frame and animated-image imports are not supported yet.

## Outfit candidate checks

The [original outfit example](examples/outfit/) provides four distinct asymmetric 64×64 walking frames, a jacket reference, a source-authored clothing mask, and [acceptance criteria](examples/outfit/acceptance.json). Regenerate it into a fresh directory:

```sh
python -m examples.generate_outfit_fixture --out output/outfit-source
```

To check a separately obtained candidate sheet and manifest:

```sh
frametailor validate examples/outfit/animation.json output/candidate/animation.json \
  --profile outfit-regions-v1 --editable-mask examples/outfit/editable-mask.png
```

The default `exact-alpha-v1` profile preserves every alpha byte. The separate `outfit-regions-v1` profile permits RGB/alpha edits only in white mask regions; every other pixel and all animation metadata must remain identical. It rejects unchanged/source-substituted frames and new duplicates of originally distinct stored poses. Identical pixels are allowed where stored source poses were already identical. Repeated animation references are metadata, not additional stored identities; all such metadata must remain unchanged. It never repairs or realigns output.

A successful outfit check means only that these constraints passed. Reports always set `approved: false`; pose, movement, genuine garment changes, cross-frame identity/clothing consistency and provider success still require review. Provider logs must establish successful completion and correct response order; pixel checks cannot establish that. Masks must be static binary grayscale PNGs authored against the source, never derived from a candidate. Provenance hashes are recorded but not automatically verified.

## Masked compositing

```sh
frametailor composite examples/outfit/animation.json output/candidate/animation.json \
  --editable-mask examples/outfit/editable-mask.png --out output/composited
```

Candidate metadata and sheet dimensions must match the source. White mask pixels copy all candidate RGBA channels exactly; black pixels preserve all source channels exactly. Transparent candidate pixels can erase source art inside the mask, and white regions over transparent source permit explicit silhouette expansion. Masks are never enlarged; there is no alpha blending, cleanup or automatic alignment.

Select `--mode preserve-source-silhouette` to preserve every source alpha byte and copy only candidate RGB inside authorized visible source pixels. Protected source RGBA and fully transparent source pixels (including hidden RGB) remain exact. Nonzero candidate alpha supplies RGB without blending, even if partially transparent. A zero-alpha candidate pixel on authorized visible source is missing color: the default `--missing-candidate error` refuses it. Explicit `--missing-candidate source` retains source color there, recording the exact count, sheet coordinates, frame-local coordinates and `source-colour-fallback.png` map. This can retain unwanted source clothing and requires visual review. The default `--mode rgba-replace` keeps the original behavior.

The review bundle retains the input candidate separately and includes frame PNGs, an APNG preview, a three-column original/candidate/composite comparison, pixel-change maps, per-frame transfer/discard counts, both validation profiles and provenance hashes. `transferred_changed_pixels` counts selected pixels that differ from source; `authorized_pixels` also includes unchanged copies. Discarded-expansion maps flag possible clipping, not semantic clothing loss. Inspect collar, sleeve and hem boundaries yourself. Exit status reports outfit structural validity, and `approved` always remains false. Exact-alpha may fail for authorized clothing/silhouette changes.

Transfer maps count actual result changes; discard maps count unauthorized candidate changes. Silhouette mode reports actual alpha changes separately and leaves them at zero. Fallback and silhouette preservation are structural operations and never grant artistic approval. The catalog APNG follows stored-frame order; target-specific animation references and flips require a target-aware preview.

All example artwork is original and programmatically drawn under [Apache-2.0](LICENSE). No game artwork, engine dependencies, service credentials or paid providers are included.

## Native-grid outfit pilot

Generate an original 16×32 courier's nine stored walking poses, then apply a blue zip jacket and ochre bib overalls with one shared character template:

```sh
python -m examples.native_walk --out output/native-walk
frametailor outfit output/native-walk/source/animation.json \
  --template output/native-walk/source/template.json \
  --outfit output/native-walk/source/overalls.json --out output/another-outfit
```

The [example source](examples/native_walk.py) draws the fixture and authors its template together. A static grayscale region sheet uses zero for protected pixels and nonzero ids for semantic material/shade roles (torso, sleeves, legs). The template binds the source sheet hash and animation structure; each stored pose supplies a view, integer anchors and an explicit `exceptions` list. An outfit supplies material colors and view-specific detail pixels as `[dx, dy, "material.shade"]`, relative to those anchors. Each detail declares its permitted region ids. The generated JSON files are runnable schema examples; no model, service or candidate image is involved.

All six region labels and all 24 pose anchors are reused for the second outfit. Details are defined three times per outfit (front/back/side), with no per-frame outfit definitions or output painting. The example needs zero frame exceptions. Its four directional APNG comparisons follow explicit walk references, including repeated idle poses and mirrored east; the compositor's separate catalog preview follows stored order.

The jacket has a collar, zipper, pocket openings, cuffs and back yoke. The overalls have a bib, straps, clasps and back braces. These occupy native pixels without resampling. Out-of-region, overlapping, invisible or out-of-frame details fail rather than being clipped. A global source-color split check rejects palette-only results; this diagnostic cannot establish garment quality. An optional visible-color budget checks counts only, without quantization or engine palette encoding.

The renderer reuses silhouette compositing and both existing validation profiles, preserves all animation metadata, and records detail coordinates, material contrast, exceptions, inputs and hashes in the review bundle. Bundled `inputs/animation.json`, `template.json` and `outfit.json` can replay it. Reports keep `approved: false`. Review `comparison-native.png`, `comparison-10x.png` and `walking-{direction}-{1,10}x.png` for readability and clothing consistency.

This bounded pilot preserves source silhouettes; future garments need not. New characters still require source-authored regions and pose anchors. The example's rigid torso and visible cuffs avoid difficult occlusion: long coats, loose sleeves, asymmetric accessories and changing depth order would need additional design and pose-specific review. Mirrored directions also mirror asymmetric details. This is neither animation synthesis nor a game exporter. Generated bundles and private external assets belong in ignored local directories.

## Cross-character walking example

```sh
python -m examples.portable_walk --out output/portable-walk
```

The [independent mechanic source](examples/independent_walk.py) draws a broader body, smaller angular head, sloping shoulders, splayed boots and different arm/hip poses without importing the courier or any template definitions. Its [separate template authoring](examples/portable_walk.py) applies the courier's jacket JSON unchanged: all materials and front/back/side detail geometry are shared. The body still accommodates the fixed six-by-eight-pixel torso pattern; this example does not adapt garment geometry to arbitrary proportions or depth changes. Both sources remain usable on their own, with separate animation timing and protected features.

`frametailor.native.regions_from_colors(sheet, {"#c37842": 1, ...})` creates an L-mode region sheet from explicit source RGB assignments. Unmapped and transparent pixels stay protected; absent colors and invalid assignments fail. It does not classify anatomy. Use it only when selected colors belong exclusively to their declared clothing roles across every source pose. Shared colors on hair, skin, accessories or occluding parts require separately authored regions. Inspect all resulting masks before binding the template to the source hashes.

Preparing this mechanic uses six color assignments instead of painting nine region masks, plus 24 manually placed pose anchors. All nine region maps still require review. The unchanged jacket needs zero view adjustments, frame exceptions or output pixel corrections; its 147 detail placements transfer to each character. Existing overalls also render with the same mechanic template, checked by the regression suite. These are bounded structural results, with `approved: false`; native-size comparisons and four directional APNGs remain separate visual evidence. Semantic ownership, anchor placement and occlusion review remain character-specific work.
