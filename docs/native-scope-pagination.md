# Revision-scoped Native SDK observation paging

The Motionwright Native SDK exposes application-owned project observations through the exact pinned Semwright `ObservationProvider`, not through a second database API or generic filesystem read capability.

## Previous truncation

Before this change, the `timeline`, `canvas`, `deliverables`, `alternatives`, `locks`, `branches`, `reviews`, and `merges` scopes evaluated only the first `query.limit` elements and then returned `complete: true` and `next: null` even when more elements existed. An agent viewing a long project would have incorrectly believed it had enumerated all scenes or semantic objects. The smaller `summary`, `brief`, `narrative`, `audio`, and `visual-language` singleton scopes shared the same no-cursor behavior without explicitly rejecting forged continuations.

## Contract

For application-owned arrays and singleton projections, the provider now returns a bounded page of up to the requested `Query.limit`, with an actual `PageCursor` when more rows remain. Each cursor encodes a normalized `offset:<positive integer>`, and the SDK already binds it to the **exact resource, generation, revision, and observation scope**. These offsets reference a single versioned in-memory `Project` snapshot; they are not used to read a mutable SQL journal.

Every follow-up request must present the returned cursor in a new `Query` for the **same** scope and resource. The provider rejects wrong scope, wrong generation/revision, malformed/zero/leading-zero/terminal offsets, an offset that exceeds the array, or a token from `history` / `history-recent` under the wrong cursor grammar. A continuation always advances: there are no empty terminal pages or same-token loops.

The provider also checks the authoritative project generation/revision again *after* building the page. If a different editor or native agent commits while a page is being read, the attempt returns `StaleReference` rather than claiming that older content is CURRENT. After any creative edit, callers should begin a new enumeration from a fresh observation; they must not reuse the old cursor.

The existing application-owned journal scopes remain distinct and **unchanged**: `history` uses oldest-first, SQLite-backed keyset cursors; `history-recent` uses newest-first SQLite keyset cursors. Their cursor tokens are `after-revision:<r>` and `through-revision:<r>` respectively, not array offsets. See [history pagination](history-pagination.md).

## Production jobs: intentionally partial, not complete

`production-jobs` is **not** a versioned project array. Its receipt ledger can receive new observations without advancing the creative project revision. The current service also loads only its newest 256 receipts before deriving job summaries. A project-revision-bound offset cursor would therefore be deceptive, with missing/duplicated jobs across reads or unseen older receipts.

For now this scope returns the same bounded latest-window job projection with `complete: false` and **no cursor**, which the pinned Semwright Native SDK explicitly defines as **partial/unknown coverage**, never as complete enumeration. A forged cursor request is rejected. Full, replay-safe pagination will require a separate receipt-watermark/keyset contract and should not be claimed until implemented.

## Evidence and boundaries

- Rust Native SDK tests paginate 271 synthetic items with multiple page sizes and verify every index exactly once, ordered and complete; malformed/misaligned/stale continuations fail closed.
- Integration tests write 11 actual scenes to the application service and exhaust both `timeline` and `canvas` with 3-item pages, preserving every scene ID. A subsequent creative change invalidates the old cursor.
- Tests verify a truly empty `production-jobs` window **still cannot prove historical completeness**, and forged receipt offsets are refused.
- `ObservationPage::validate_for` remains enforced at the exact Semwright pinned SDK SHA; no capability count, authorization, scheduler, runtime driver, native renderer or application storage schema is changed.

Tests of cursor correctness are **implementation/contract evidence**, not independent human product acceptance. The private product ledger remains separate.
