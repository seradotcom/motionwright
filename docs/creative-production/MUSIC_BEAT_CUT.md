# MW05-E08-01: Music beat clock and scoped editorial cut

The Production Plan supports an editor-marked music grid tied to an existing audio asset identifier and SHA-256. Editors may enter exact rational seconds, draft beats from a declared BPM, save the grid as part of the revisioned plan, and inspect one scene-cut change before committing it.

A cut changes the selected scene duration and ripples later scene starts using exact rational arithmetic. It preserves canvas object identities and scene-local keyframes. The operation rechecks project revision and source digest, timing locks on the project and downstream scenes, delivery-profile timing locks, and author-created beat/keyframe boundaries.

Global audio cues, transcript segments and markers do not move automatically. The inspector reports affected counts. Applying an edit requires explicit acknowledgement where those anchors are affected. Every scene whose start or duration changed becomes Draft and the previous plan approval is cleared. Scene-linked semantic SFX move with their authored scenes, but a preview refuses edits that truncate their original source or release tails and reports cues requiring listening review.

The change is a single StudioService transaction, also exposed through the pinned Semwright Native SDK as scene.music.cut.snap. Both the client preview and trusted Rust domain validate the impact independently. No new renderer or parallel backend is added.

This is not automatic beat detection, an audio time stretch, an audio mix, a soundtrack rights check, or evidence of human approval. Actual synchronized native media output remains a separate acceptance gate.

Rust domain, service persistence, Native SDK operation, browser-mode editing, Studio unit contracts and Chromium regression tests run through the dedicated music-clock CI workflow. MW05-E08-01 remains partial until wider soundtrack and human validation are completed.
