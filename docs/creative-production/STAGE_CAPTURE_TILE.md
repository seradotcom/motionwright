# MW05-E07 · First-party capture tile in original ProductStage

Status: **bounded implementation, pending exact-HEAD CI and real native image rendering**.
This is not full E07-01/E07-02, a third-party software capture, legal
permission or human visual acceptance.

## An admitted source, not a fabricated product screen

The existing Production → Capture sources workflow registers a first-party
Chromium source receipt as three immutable CAS assets: screenshot PNG,
capture manifest JSON and original fixture-state JSON. The receipt pins
the source project, generation/revision, authored claim, Git commit, viewport,
redaction and independent screenshot digest. SQLite reads and compares these
bytes before registration. This is not authority to copy commercial UIs.

ProductStage can now attach an existing registered capture to one of its
generic, original and separately editable scenes. A StageCaptureCard contains
only its version, ProductStage instance ID and source capture ID. No URI
strings, executable HTML, opaque scripting fields or unreviewed image paths.
Only previously admitted first-party synthetic source receipts are accepted;
one tile per stage and 16 per project.

Change::BindStageCapture and Change::DetachStageCapture use existing
StudioService revision CAS and Native SDK capabilities product-stage.capture.bind
and product-stage.capture.detach. Scene locks and content policies remain active.
Detaching ProductStage with a live source tile fails until the tile is
released; releasing the tile retains every original Canvas node and
the immutable source/claim provenance.

## Native projection and editability

In canonical Film, the original Stage card rectangle remains a normal,
editable Canvas shape. A separate SubjectContent::Image is added immediately
after it on the existing layer. Its asset ID resolves to the imported
redacted PNG's content-addressed AssetRef with exact SHA-256, media type and
source provenance. Placement derives from the authored card's position and
size so compatible edits retain alignment. MediaFit::Contain with its original
1440 × 940 ratio preserves the entire source frame: no guessed crop,
fabricated screenshot or second rendering system.

Only the source tile is raster-backed. Other Stage elements (heading, detail,
action, focus, chrome and disclosure) remain separate native editable
subjects with their original IDs and optional keyframes. The Stage notice,
ORIGINAL UI GRAPHIC / NOT A PRODUCT CAPTURE, remains visible while bound.
The linked source is explicitly first-party and synthetic, not an authentic
client-app or real customer demonstration.

The SVG Studio preview does not invent the PNG's pixels: it shows authored
Stage objects plus source metadata and explains that media pixels require
native rendering. Editorial preview is not independent image evidence.

## Acceptance and unsupported features

Rust domain, Studio and Native Film tests cover source admission, missing
capture, stable Canvas objects/camera, preserved source assets, locks,
detach, copy updates, and genuine Film image subject/AssetRef creation
without replacing it by a synthetic rectangle. Native Film realization
is checked; CI must still verify real H.264 image pixels via the pinned
provider. A passing Film contract is not itself a passing media render.

A provider rejecting media source bytes must fail explicitly; no rights
workaround or substitute screenshot is used. Still open: authorized external
application capture, per-frame screen recordings, calibrated masks/occlusion,
3D device rigs and lighting, image-to-GLB projection, human creative and rights
approval. E07 remains partial until these are independently accepted.
