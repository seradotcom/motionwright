import assert from "node:assert/strict";
import { test } from "node:test";
import vm from "node:vm";
import { installSyntheticProjectReads } from "../tests/support/syntheticProjectReads.ts";

const id = "01900000-0000-7000-8000-000000000001";
const generation = "01900000-0000-7000-8000-000000000002";
function fixture() {
  return {
    project: { id, generation, revision: 12, schema_version: 2, title: "Explicit fixture", scenes: [{ id: "scene" }] },
    native_sdk: { application: "motionwright", mode: "tauri", pinned_revision: "a".repeat(40), version: "1.0.0" },
  };
}
function install() {
  globalThis.window = {};
  installSyntheticProjectReads();
  return window.__SYNTHETIC_PROJECT_READ__;
}
const request = project_id => ({ request: { project_id } });

test("unselected bootstrap exposes only the supplied SDK and no project fallback", () => {
  const read = install(), source = fixture();
  const result = read("bootstrap", request(null), source);
  assert.deepEqual(result, { project: null, native_sdk: source.native_sdk, project_selection: { status: "unselected" } });
  assert.notEqual(result.native_sdk, source.native_sdk);
  assert.equal(source.project.id, id);
});

test("explicit selected bootstrap preserves the caller's snapshot without aliasing it", () => {
  const read = install(), source = fixture();
  const result = read("bootstrap", request(id), source);
  assert.deepEqual(result.project, source.project);
  assert.deepEqual(result.project_selection, { status: "selected", project_id: id });
  result.project.title = "Changed reply";
  assert.equal(source.project.title, "Explicit fixture");
});

test("projection head uses the current identity, decimal revision and actual scene count", () => {
  const read = install(), source = fixture();
  source.project.revision = Number.MAX_SAFE_INTEGER;
  source.project.scenes.push({ id: "second" });
  assert.deepEqual(read("project_projection_head", request(id), source), {
    identity: { project_id: id, generation, revision: "9007199254740991" },
    schema_version: 2, title: "Explicit fixture", scene_count: 2,
  });
});

test("full-project read returns the caller's complete clone", () => {
  const read = install(), source = fixture();
  source.project.extraFixtureField = { preserved: true };
  const result = read("open_project", request(id), source);
  assert.deepEqual(result, source.project);
  result.scenes.push({ id: "changed-reply-only" });
  assert.equal(source.project.scenes.length, 1);
});

test("missing, malformed and foreign identities never produce fallback reads", () => {
  const read = install(), source = fixture();
  for (const command of ["bootstrap", "project_projection_head", "open_project"]) {
    for (const args of [undefined, {}, { request: {} }, request(undefined), request("bad"),
      request("01900000-0000-7000-8000-000000000009")]) {
      assert.throws(() => read(command, args, source), /explicit|matching/);
    }
  }
  for (const command of ["project_projection_head", "open_project"]) {
    assert.throws(() => read(command, request(null), source), /matching/);
  }
});

test("the adapter cannot issue effects or answer arbitrary commands", () => {
  const read = install();
  for (const command of ["issue_effect_grant", "apply_change", "render_motion_canvas", "project_scene_page", ""]) {
    assert.throws(() => read(command, request(id), fixture()), /Unsupported/);
  }
});

test("invalid fixture authority cannot be replaced with fixed identities or revisions", () => {
  const read = install();
  for (const [key, value] of [["id", "bad"], ["generation", "bad"], ["revision", -1],
    ["revision", Number.MAX_SAFE_INTEGER + 1], ["schema_version", 0], ["scenes", null]]) {
    const source = fixture();
    source.project[key] = value;
    assert.throws(() => read("project_projection_head", request(id), source), /complete project identity/);
  }
});

test("serialized installation has no external runtime dependency or Tauri interception", () => {
  const bridge = { invoke() { throw new Error("Must not be intercepted"); } };
  const isolatedWindow = { __TAURI_INTERNALS__: bridge };
  vm.runInNewContext("(" + installSyntheticProjectReads.toString() + ")()", {
    window: isolatedWindow, structuredClone,
  });
  assert.equal(isolatedWindow.__TAURI_INTERNALS__, bridge);
  assert.equal(isolatedWindow.__SYNTHETIC_PROJECT_READ__("open_project", request(id), fixture()).id, id);
});

test("successive callers retain independent current snapshots without a hidden cache", () => {
  const read = install(), first = fixture(), second = fixture();
  second.project.title = "Second caller";
  second.project.revision = 13;
  assert.equal(read("open_project", request(id), second).revision, 13);
  assert.equal(read("open_project", request(id), first).revision, 12);
  first.project.revision = 14;
  assert.equal(read("project_projection_head", request(id), first).identity.revision, "14");
  assert.equal(read("open_project", request(id), second).title, "Second caller");
});

test("an explicit receiver supports Node callbacks without a browser global", () => {
  const previousWindow = Object.getOwnPropertyDescriptor(globalThis, "window");
  delete globalThis.window;
  try {
    const receiver = {};
    const read = installSyntheticProjectReads(receiver);
    assert.equal(Object.hasOwn(globalThis, "window"), false);
    assert.equal(receiver.__SYNTHETIC_PROJECT_READ__, read);
    const source = fixture();
    assert.deepEqual(read("bootstrap", request(null), source).project_selection, { status: "unselected" });
    assert.equal(read("project_projection_head", request(id), source).identity.revision, "12");
    assert.deepEqual(read("open_project", request(id), source), source.project);
    source.project.revision = 13;
    assert.equal(read("open_project", request(id), source).revision, 13);
  } finally {
    if (previousWindow) Object.defineProperty(globalThis, "window", previousWindow);
    else delete globalThis.window;
  }
});
