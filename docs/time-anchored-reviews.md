# Time-anchored creative reviews

Review anchors use Motionwright's existing versioned creative review domain; there is no parallel comment store or runtime authority.

- **Resource only** (default): preserves the existing behavior, anchoring a comment to a branch, project/scene resource and its revision.
- **Current playhead:** stores the current absolute project time as `ReviewAnchor.start` with `end = null`.
- **Explicit project range:** stores `ReviewAnchor.start` and `end` with the domain's rational timestamp representation. The editor validates finite nonnegative values, ordering, and a bound based on the selected scene or whole project timeline; the domain independently validates the native mutation.
- **Scope:** the editor defaults to the selected scene. For a comment spanning scene boundaries, the reviewer must explicitly select whole-project scope; the UI does not silently save out-of-scope timestamps under a scene resource.
- **Jump to time:** reads the persisted anchor, seeks the shared editorial clock and never edits project state. Jumping to a review from another branch is disabled until that branch is active.

A review's status and branch/revision remain explicit. For `needs_recheck` reviews, the UI warns that the stored time may describe different content today; jumping back is not acceptance, verification, or historical frame reconstruction. This editor does not claim pixel/object-level review anchors, native frame readback or canonical Effect Conformance.

Browser tests cover time-point and range admission, invalid ranges, navigation back to an anchor without creating a revision, and warnings after content is edited. CI browser-demo evidence does not constitute independent product acceptance.
