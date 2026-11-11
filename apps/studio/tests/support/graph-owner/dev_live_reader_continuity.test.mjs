//! Pure source-bound custody invariants, NOT a Native client, Save, or R74 admission.
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { test } from 'node:test';
import { requireClosedDevelopmentStoreContinuity } from './dev_live_reader.mjs';

const ANCHOR = '/private/g1/store/blobs/.motionwright-cas-root-v1.json';
function fixture() {
  const entries = [
    { path: '', kind: 'directory' },
    { path: 'motionwright.sqlite3', kind: 'file', sizeBytes: 900, sha256: 'a'.repeat(64) },
    { path: 'blobs', kind: 'directory' },
    { path: 'blobs/.motionwright-cas-root-v1.json', kind: 'file',
      sizeBytes: 80, sha256: 'b'.repeat(64) },
    { path: 'blobs/sha256/11/' + '1'.repeat(64), kind: 'file',
      sizeBytes: 137, sha256: '1'.repeat(64) },
    { path: '.motionwright-cas-original', kind: 'directory' },
    { path: '.motionwright-cas-original/catalog.json', kind: 'file',
      sizeBytes: 270, sha256: 'c'.repeat(64) },
  ];
  const digest = createHash('sha256').update(JSON.stringify(entries)).digest('hex');
  const capture = { schemaVersion: 1, entries, sourceBeforeSha256: digest,
    sourceAfterSha256: digest, snapshotSha256: digest };
  const before = { entries: structuredClone(entries), cas_root_id: 'original' };
  const after = structuredClone(before);
  const originalAnchor = { path: ANCHOR, device: '2049', inode: '123456' };
  return { capture, before, after, originalAnchor,
    reopenedAnchor: structuredClone(originalAnchor) };
}
function check(caseValues) {
  const { capture, before, after, originalAnchor, reopenedAnchor } = caseValues;
  return requireClosedDevelopmentStoreContinuity(capture, before, after,
    originalAnchor, reopenedAnchor);
}

test('closed original SQLite/CAS inventory and physical anchor remain identical', () => {
  assert.equal(check(fixture()), true);
});

test('reject closed writer manifest with changed digest or conflicting copy', () => {
  const altered = fixture();
  altered.capture.sourceAfterSha256 = 'f'.repeat(64);
  assert.throws(() => check(altered), /ORIGINAL_CAS_CAPTURE_UNAVAILABLE/);
  const forged = fixture();
  forged.capture.entries[3].sha256 = 'd'.repeat(64);
  assert.throws(() => check(forged), /ORIGINAL_CAS_CAPTURE_UNAVAILABLE/);
});

test('reject changes to the original CAS before opening the read-only process', () => {
  const replaced = fixture();
  replaced.before.entries[6].sha256 = 'd'.repeat(64);
  assert.throws(() => check(replaced), /STORE_CHANGED_SINCE_WRITER_CAPTURE/);
});

test('reject CAS catalog, orphan or SQLite changes during independent readback', () => {
  const catalog = fixture();
  catalog.after.entries[6].sizeBytes = 271;
  assert.throws(() => check(catalog), /STORE_CHANGED_DURING_INDEPENDENT_READ/);
  const addedSibling = fixture();
  addedSibling.after.entries.push({ path: 'blobs/unexpected', kind: 'file',
    sizeBytes: 1, sha256: 'f'.repeat(64) });
  assert.throws(() => check(addedSibling), /STORE_CHANGED_DURING_INDEPENDENT_READ/);
  const sqlite = fixture();
  sqlite.after.entries[1].sha256 = '0'.repeat(64);
  assert.throws(() => check(sqlite), /STORE_CHANGED_DURING_INDEPENDENT_READ/);
});

test('reject replacement or loss of the managed CAS anchor physical identity', () => {
  const replaced = fixture();
  replaced.reopenedAnchor.inode = '123457';
  assert.throws(() => check(replaced), /ORIGINAL_CAS_ANCHOR_REPLACED/);
  const missing = fixture();
  missing.originalAnchor.path = '/unrelated/other-anchor.json';
  assert.throws(() => check(missing), /ORIGINAL_CAS_ANCHOR_UNAVAILABLE/);
  const root = fixture();
  root.after.cas_root_id = 'replacement';
  assert.throws(() => check(root), /ORIGINAL_CAS_CAPTURE_UNAVAILABLE/);
});
