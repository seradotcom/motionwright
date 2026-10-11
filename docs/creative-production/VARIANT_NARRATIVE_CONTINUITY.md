# Variant continuity preflight

This read-only reviewer explains scene omissions and authored claim continuity before export.

The analysis compares a delivery draft with its explicit parent cut, or with the full source when no parent exists. It uses exact rational arithmetic for the retained duration and includes the project revision, generation, profile and selected scene IDs.

It flags missing, duplicated or reordered scenes; omitted protected scenes; claims with no retained supporting shot; missing production-plan decisions; localization gaps; and transcript segments crossing a retained scene boundary. Source and claim references are matched to content-addressed assets, but no factual truth or license status is inferred.

This panel is advisory. Saving a profile still passes through the existing Semwright Native SDK and Rust domain validation. It adds no new renderer, backend or release authority. A clean report does not mean the video was rendered, checked for rights, visually approved or understood by viewers.

The test suite covers source-scoped claims, protected scenes, exact cut duration, non-empty localization, caption cut boundaries, source identity and read-only behavior. Chromium checks the live draft interaction without committing a new project revision. The original acceptance suite and human creative review remain pending, so requirement MW05-E14-02 stays partial.
