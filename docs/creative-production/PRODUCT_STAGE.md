# MW05-E07-01 · Generic ProductStage with editable screen anchors

Status: **partial v0.5 feature**, not a 3D device stage or a real app
capture. A green code test is not creative approval or media-rights evidence.

## Implemented in this branch

Production → ProductStage is a workstation inside the existing Studio,
SQLite project journal and Semwright Native SDK. It offers three original
unbranded 2D semantic silhouettes: browser, flat_panel, generic_handset.
These are not modeled on commercial hardware or a trademarked app.

Each stage is exactly 14 normal, independently editable Canvas nodes:
outer frame, screen, chrome, indicator, headline, detail, card, card highlight,
action button/label, focus marker/caption, neutral frame caption and the
mandatory disclaimer. Role IDs derive from the same versioned instance ID
across content replacement and output aspect. Screen-relative focus anchors
are integer thousandths, not guessed absolute device pixels.

Original copy replacement reuses the existing three-way component merge and
preserves compatible human styling, screen/focus geometry and scene camera.
An authored conflicting override, property lock, missing object or too-wide
copy fails rather than flattening the scene, inventing data or shrinking type.
The optional focus entrance is existing native Y/opacity keyframe motion;
the pinned Film path uses the original fixed-container text projection to
prevent silently missing native glyphs.

Three source layouts are deterministic across 16:9, 9:16 and 1:1. The
editorial Studio SVG preview is not evidence of a native screenshot, device
camera or successful MP4. Heavy Rust, Studio, browser and Native Film tests
must pass on the exact pushed source revision before technical acceptance.

## Rights and evidence

The user must author a bounded source-rights declaration before creating
this semantic stage. The declaration is a human statement, NOT authenticated
intellectual-property permission. Configuration denies unknown fields and
does not accept external images, source paths, logos, executable HTML/CSS,
shaders or branded hardware models. Real imported assets cannot silently
enter the composition via this operation.

While attached, the permanently included editable native disclosure text is:

ORIGINAL UI GRAPHIC / NOT A PRODUCT CAPTURE

Removing or falsely replacing that text invalidates an attached ProductStage
and prevents a new committed project revision. Detaching retains all ordinary
Canvas objects, but revokes their ProductStage ownership/evidence.

Real screenshot/file ingestion, exact original visual asset bytes, source
rights attestation, device/3D camera, occlusion, optical realism and capture
reproducibility belong to later requirements; no third-party media is copied
or presented as the tested software here. A user can falsely assert authorship:
this implementation cannot automatically audit license ownership.

## Contracts and tests

Application revisions use Change::UpsertProductStage and
Change::DetachProductStage and two corresponding public Native SDK operations:
product-stage.upsert and product-stage.detach. No second Core/backend or
provider is introduced. The API schema bounds every parameter and rejects
unrecognized asset/model fields.

Source tests cover three silhouettes × three aspect families, stable
screen/focus IDs, authored text replacement, preserved camera, manually
overridden styles, motion keys, baseline/lock conflicts, missing disclosure,
source-rights declaration, layout bounds, removed object detection, detach,
and native Film semantic subjects with actual glyph child text.

Acceptance remains **PARTIAL / NOT_COMPLETED**. An app capture with redactions,
viewport, version and exact scenario reproducibility is MW05-E07-02.
