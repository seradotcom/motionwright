#!/usr/bin/env node
// Run the real bundled Vite editor path with filesystem/process effects replaced.
// This component test simulates Windows and WSL; it never invokes an editor or shell.
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const options = new Map();
for (let index = 2; index < process.argv.length; index += 2) {
  const key = process.argv[index];
  assert(["--bundle", "--report"].includes(key), "unknown option: " + key);
  assert(process.argv[index + 1] && !options.has(key), "missing or duplicate option");
  options.set(key, process.argv[index + 1]);
}
const bundle = options.get("--bundle") ??
  fileURLToPath(new URL("../apps/studio/node_modules/vite/dist/node/chunks/config.js", import.meta.url));
const repositoryRoot = fileURLToPath(new URL("../", import.meta.url));
const probeBytes = fs.readFileSync(fileURLToPath(import.meta.url));
const studioPackageBytes = fs.readFileSync(path.join(repositoryRoot, "apps/studio/package.json"));
const lockBytes = fs.readFileSync(path.join(repositoryRoot, "apps/studio/package-lock.json"));
const lockedViteVersion = JSON.parse(lockBytes.toString("utf8")).packages["node_modules/vite"].version;
let installedViteVersion = null;
if (!options.has("--bundle")) {
  const installedPackage = JSON.parse(fs.readFileSync(
    path.join(repositoryRoot, "apps/studio/node_modules/vite/package.json"), "utf8"));
  assert.equal(installedPackage.name, "vite");
  installedViteVersion = installedPackage.version;
  assert.equal(installedViteVersion, lockedViteVersion,
    "installed Vite version differs from the reviewed lock");
}
const sourceShaFromCiEnvironment = process.env.GITHUB_SHA ?? null;
assert(sourceShaFromCiEnvironment === null || /^[0-9a-f]{40}$/.test(sourceShaFromCiEnvironment),
  "invalid CI source SHA");
const bytes = fs.readFileSync(bundle);
const source = bytes.toString("utf8");
const hash = (value) => createHash("sha256").update(value).digest("hex");
const regions = [...source.matchAll(
  /\/\/#region [^\r\n]*\/launch-editor@([^/\r\n]+)\/node_modules\/launch-editor\/([^\r\n]+)\r?\n([\s\S]*?)\/\/#endregion/g,
)];
assert(regions.length > 0, "no reviewed launch-editor regions in installed Vite bundle");
const versions = new Set(regions.map((match) => match[1]));
assert.equal(versions.size, 1, "mixed bundled launch-editor versions");
assert.equal(regions.filter((match) => match[2] === "index.js").length, 1,
  "launcher entry point must be unique");
const moduleSource = regions.map((match) => match[3]).join("\n");
const positiveCases = [
  { name: "ordinary_path", file: "C:\\editor-fixture\\scene.ts",
    command: "code.cmd C:\\editor-fixture\\scene.ts" },
  { name: "spaces_keep_quoting", file: "C:\\editor-fixture\\two scenes.ts",
    command: 'code.cmd "C:\\editor-fixture\\two scenes.ts"' },
  { name: "ampersand_keeps_escaping", file: "C:\\editor-fixture\\scene&notes.ts",
    command: 'code.cmd ^"C:\\editor-fixture\\scene^&notes.ts^"' },
  { name: "caret_keeps_escaping", file: "C:\\editor-fixture\\scene^notes.ts",
    command: 'code.cmd ^"C:\\editor-fixture\\scene^^notes.ts^"' },
  { name: "line_column_keep_editor_arguments", file: "C:\\editor-fixture\\scene.ts:12:3",
    command: "code.cmd -r -g C:\\editor-fixture\\scene.ts:12:3" },
];
const rejectedCases = [
  { name: "percent_environment_token_rejected", file: "C:\\editor-fixture\\scene%EDITOR_PROBE_SENTINEL%.ts",
    error: /argument containing "%"/ },
  { name: "percent_commandline_token_rejected", file: "C:\\editor-fixture\\scene%CMDCMDLINE:~-38%.ts",
    error: /argument containing "%"/ },
  // These two exercise synthetic arguments, not legal Windows filesystem names.
  { name: "synthetic_carriage_return_rejected", file: "C:\\editor-fixture\\scene\rmarker.ts",
    error: /argument containing a line break/ },
  { name: "synthetic_line_feed_rejected", file: "C:\\editor-fixture\\scene\nmarker.ts",
    error: /argument containing a line break/ },
];

function observe(file, fixture = {}) {
  const platform = fixture.platform ?? "win32";
  const editor = fixture.editor ?? "code.cmd";
  const virtualCwd = platform === "linux" ? "/mnt/c/editor-fixture" : "C:\\editor-fixture";
  const flavor = platform === "linux" ? path.posix : path.win32;
  const virtualPath = Object.freeze({
    ...flavor,
    resolve: (...parts) => flavor.resolve(virtualCwd, ...parts),
    relative: (from, to) => flavor.relative(
      flavor.resolve(virtualCwd, from), flavor.resolve(virtualCwd, to)),
  });
  const executions = [];
  const errors = [];
  const messages = [];
  const fileChecks = [];
  const unexpectedEffects = [];
  const child = {
    on() { return this; },
    kill(...args) {
      unexpectedEffects.push({ operation: "kill", args });
      throw new Error("unexpected process termination");
    },
  };
  const childProcess = Object.freeze(Object.fromEntries([
    "exec", "execFile", "execSync", "execFileSync", "spawn", "spawnSync", "fork",
  ].map((operation) => [operation, (...args) => {
    executions.push({ operation, args });
    if (operation !== (platform === "win32" ? "exec" : "spawn")) {
      throw new Error("unexpected process primitive: " + operation);
    }
    return child;
  }])));
  const fakeFs = Object.freeze({
    existsSync(value) {
      fileChecks.push(value);
      return !value.endsWith("\\missing.ts");
    },
  });
  const fakeOs = Object.freeze({ release: () => fixture.wsl ? "5.15.0-Microsoft" : "fixture" });
  const allowed = new Map([
    ["fs", fakeFs], ["os", fakeOs], ["path", virtualPath], ["child_process", childProcess],
  ]);
  const context = vm.createContext({
    __commonJSMin(factory) {
      let cached;
      return () => {
        if (!cached) {
          cached = { exports: {} };
          factory(cached.exports, cached);
        }
        return cached.exports;
      };
    },
    __require(name) {
      assert(allowed.has(name), "unadmitted module in editor probe: " + name);
      return allowed.get(name);
    },
    // Only the explicitly supplied one-token editor is selected. No discovery.
    require_shell_quote: () => ({ parse: (value) => {
      assert.equal(value, editor);
      return [value];
    } }),
    require_picocolors: () => ({ red: (value) => value }),
    process: Object.freeze({
      platform, cwd: () => virtualCwd, env: Object.freeze({}), versions: Object.freeze({}),
    }),
    console: Object.freeze({ log: (...args) => messages.push(args) }),
    fixtureFile: file,
    fixtureEditor: editor,
    fixtureError: (fileName, message) => errors.push({ fileName, message }),
  }, { codeGeneration: { strings: false, wasm: false } });
  vm.runInContext(moduleSource +
    "\nrequire_launch_editor()(fixtureFile, fixtureEditor, fixtureError);",
    context, { timeout: 1000, filename: "verified-vite-launch-editor-regions.js" });
  return { simulated_platform: fixture.wsl ? "linux-wsl" : platform,
    executions, errors, messages, fileChecks, unexpectedEffects };
}

const results = [];
function check(name, file, oracle, fixture = {}) {
  let observation;
  try {
    observation = observe(file, fixture);
    assert.equal(observation.unexpectedEffects.length, 0);
    oracle(observation);
    results.push({ name, status: "PASS", observation });
  } catch (error) {
    results.push({ name, status: "FAIL", reason: error.message, observation });
  }
}
for (const test of positiveCases) {
  check(test.name, test.file, (observed) => {
    assert.equal(observed.fileChecks.length, 1);
    assert.equal(observed.errors.length, 0);
    assert.equal(observed.executions.length, 1);
    assert.equal(observed.executions[0].operation, "exec");
    assert.equal(observed.executions[0].args[0], test.command);
    assert.equal(observed.executions[0].args[1].shell, true);
  });
}
for (const test of rejectedCases) {
  check(test.name, test.file, (observed) => {
    assert.equal(observed.fileChecks.length, 1);
    assert.equal(observed.executions.length, 0,
      "unsafe argument reached the intercepted process entry");
    assert.equal(observed.errors.length, 1);
    assert.match(observed.errors[0].message, test.error);
  });
}

check("windows_parent_component_normalized_after_position",
  "C:\\editor-fixture\\nonexistent\\..:12:3", (observed) => {
    assert.deepEqual(observed.fileChecks, ["C:\\editor-fixture"]);
    assert.equal(observed.errors.length, 0);
    assert.equal(observed.executions.length, 1);
    assert.equal(observed.executions[0].operation, "exec");
    assert.equal(observed.executions[0].args[0],
      "code.cmd -r -g C:\\editor-fixture:12:3");
    assert.equal(observed.executions[0].args[1].shell, true);
  });
for (const prefix of ["+", "-"]) {
  const absoluteFile = "/mnt/c/editor-fixture/" + prefix + "scene.ts";
  check(prefix === "+" ? "wsl_plus_filename_is_positional" : "wsl_minus_filename_is_positional",
    absoluteFile, (observed) => {
      assert.deepEqual(observed.fileChecks, [absoluteFile]);
      assert.equal(observed.errors.length, 0);
      assert.equal(observed.executions.length, 1);
      const call = observed.executions[0];
      assert.equal(call.operation, "spawn");
      assert.equal(call.args.length, 3);
      assert.equal(call.args[0], "vim");
      assert.deepEqual(Array.from(call.args[1]), ["./" + prefix + "scene.ts"]);
      assert.deepEqual(Object.keys(call.args[2]), ["stdio"]);
      assert.equal(call.args[2].stdio, "inherit");
    }, { platform: "linux", editor: "vim", wsl: true });
}

check("missing_file_has_no_effect", "C:\\editor-fixture\\missing.ts", (observed) => {
  assert.equal(observed.fileChecks.length, 1);
  assert.equal(observed.executions.length, 0);
  assert.equal(observed.errors.length, 0);
});
const report = {
  schema: "motionwright.vite-editor-admission/1",
  advisories: ["GHSA-698v-4r3x-3vcv", "GHSA-587h-7wmj-h5pm", "GHSA-r825-w8mm-5pxw"],
  source_sha_from_ci_environment: sourceShaFromCiEnvironment,
  ci_run_id: process.env.GITHUB_RUN_ID ?? null,
  ci_run_attempt: process.env.GITHUB_RUN_ATTEMPT ?? null,
  source_identity_scope: "CI environment claim; original checkout/source job logs remain authoritative",
  probe_sha256: hash(probeBytes),
  studio_package_sha256: hash(studioPackageBytes),
  studio_lock_sha256: hash(lockBytes),
  locked_vite_version: lockedViteVersion,
  installed_vite_version: installedViteVersion,
  bundle_mode: options.has("--bundle") ? "external_bundle_fixture" : "installed_locked_dependency",
  bundle: path.resolve(bundle),
  bundle_sha256: hash(bytes),
  module_regions_sha256: hash(moduleSource),
  bundled_launch_editor: [...versions][0],
  actual_host_platform: process.platform,
  simulated_platforms: ["win32", "linux-wsl"],
  actual_os_processes_started: 0,
  scope: "Actual bundled editor command admission with virtual file existence and intercepted effects; not cmd.exe/editor or installed application execution",
  results,
  passed: results.filter((result) => result.status === "PASS").length,
  failed: results.filter((result) => result.status === "FAIL").length,
};
report.status = report.failed === 0 ? "PASS" : "FAIL";
const output = JSON.stringify(report, null, 2) + "\n";
if (options.has("--report")) fs.writeFileSync(options.get("--report"), output, { flag: "wx" });
process.stdout.write(output);
process.exitCode = report.failed === 0 ? 0 : 1;
