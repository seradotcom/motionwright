/**
 * Build-time evidence for the npm modules which contribute rendered code to JS
 * chunks. This is not a complete package SBOM or a redistribution decision.
 *
 * Production: sourceRoot must be the exact Git worktree; package-lock.json,
 * package.json, this plugin and physical local modules must equal HEAD blobs.
 * fixture.mode="explicit-synthetic" is only for controlled tests and is marked
 * NOT_GIT_ATTESTED in the emitted evidence.
 *
 * Sampling detects changed inputs at the hook boundaries. It is not an OS write
 * lock, tarball authenticity check, or a transaction over other build plugins.
 */
import fs from "node:fs";
import path from "node:path";
import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const SCHEMA = "motionwright.frontend-notices/1";
const PREFIX = "assets/licenses/frontend/";
const INDEX = PREFIX + "index.json";
const BOUNDS = Object.freeze({
  moduleBytes: 8 * 1024 * 1024,
  manifestBytes: 256 * 1024,
  lockBytes: 16 * 1024 * 1024,
  noticeBytes: 256 * 1024,
  noticeTotalBytes: 8 * 1024 * 1024,
  capturedNoticeBytes: 16 * 1024 * 1024,
  capturedMetadataBytes: 16 * 1024 * 1024,
  noticeFiles: 128,
  directoryEntries: 4096,
  modules: 8192,
  components: 1024,
  outputBytes: 128 * 1024 * 1024,
  gitTreeBytes: 16 * 1024 * 1024,
});
const OPTION_KEYS = new Set([
  "sourceRoot", "projectRoot", "sourceSha", "packageLockPath",
  "additionalTrackedFiles", "fixture",
]);
const sha256 = (bytes) => createHash("sha256").update(bytes).digest("hex");
const sorted = (values) => [...values].sort();
const own = (object, key) => Object.prototype.hasOwnProperty.call(object, key);

function fail(code, detail = "") {
  throw new Error("FRONTEND_NOTICES_" + code + (detail ? ": " + detail : ""));
}
function plain(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}
function canonical(value) {
  if (Array.isArray(value)) return JSON.stringify(value.map((item) => JSON.parse(canonical(item))));
  if (plain(value)) {
    const ordered = Object.create(null);
    for (const key of sorted(Object.keys(value))) ordered[key] = JSON.parse(canonical(value[key]));
    return JSON.stringify(ordered);
  }
  return JSON.stringify(value);
}
function publicPath(value) {
  if (typeof value !== "string" || !value || value.length > 1024 ||
      /[\u0000-\u001f\u007f\\:]/u.test(value) || value.startsWith("/") ||
      value.split("/").some((part) => !part || part === "." || part === "..")) {
    fail("UNSAFE_PUBLIC_PATH");
  }
  return value;
}
function relativeTo(root, target, allowRoot = false) {
  const relative = path.relative(root, target);
  if (relative === "" && allowRoot) return ".";
  if (!relative || path.isAbsolute(relative) ||
      relative === ".." || relative.startsWith(".." + path.sep)) {
    fail("OUTSIDE_ROOT");
  }
  return publicPath(relative.split(path.sep).join("/"));
}
function boundedString(value, label, limit = 1024) {
  if (typeof value !== "string" || !value || value.length > limit ||
      /[\u0000-\u001f\u007f]/u.test(value)) fail("INVALID_METADATA", label);
  return value;
}
function signature(stat) {
  return [stat.dev, stat.ino, stat.size, stat.mtimeNs, stat.ctimeNs].map(String).join(":");
}
function bytesOfOutput(item) {
  if (item.type === "chunk" && typeof item.code === "string") return Buffer.from(item.code, "utf8");
  if (item.type === "asset") {
    if (typeof item.source === "string") return Buffer.from(item.source, "utf8");
    if (item.source instanceof Uint8Array) return Buffer.from(item.source);
  }
  fail("INVALID_OUTPUT");
}
function publicSource(value) {
  if (typeof value !== "string") fail("PACKAGE_SOURCE_MISSING");
  let url;
  try { url = new URL(value); } catch { fail("PACKAGE_SOURCE_UNSUPPORTED"); }
  if (url.protocol !== "https:" || url.username || url.password ||
      url.search || url.hash || !url.hostname) fail("PACKAGE_SOURCE_UNSUPPORTED");
  return url.href;
}
function licenseDeclaration(manifest) {
  const value = manifest.license;
  if (value === undefined || value === null) return null;
  if (typeof value === "string") return boundedString(value, "license", 2048);
  if (plain(value) && typeof value.type === "string") {
    const result = { type: boundedString(value.type, "license.type", 2048) };
    if (value.url !== undefined) result.url = publicSource(value.url);
    return result;
  }
  fail("INVALID_METADATA", "license");
}
function explicitLicensePaths(manifest, declaration) {
  const values = [];
  for (const key of ["license-file", "licenseFile"]) {
    if (manifest[key] !== undefined) values.push(publicPath(manifest[key]));
  }
  const text = typeof declaration === "string" ? declaration : declaration?.type;
  const match = typeof text === "string" && /^SEE LICEN[CS]E IN (.+)$/iu.exec(text);
  if (match) values.push(publicPath(match[1].trim()));
  return sorted(new Set(values));
}

export function candidateFrontendNotices(options = {}) {
  if (!plain(options) || Object.keys(options).some((key) => !OPTION_KEYS.has(key))) {
    fail("INVALID_OPTIONS");
  }
  const fixture = options.fixture;
  if (fixture !== undefined && (!plain(fixture) ||
      fixture.mode !== "explicit-synthetic" ||
      Object.keys(fixture).some((key) => !["mode", "sourceSha", "trackedSha256", "readHead"].includes(key)) ||
      !plain(fixture.trackedSha256) ||
      (fixture.readHead !== undefined && typeof fixture.readHead !== "function"))) {
    fail("INVALID_FIXTURE");
  }
  if (fixture && (!options.sourceRoot || !options.projectRoot)) fail("INVALID_FIXTURE");
  const fixtureTracked = new Map();
  if (fixture) {
    for (const [key, digest] of Object.entries(fixture.trackedSha256)) {
      publicPath(key);
      if (typeof digest !== "string" || !/^[a-f0-9]{64}$/u.test(digest)) fail("INVALID_FIXTURE");
      fixtureTracked.set(key, digest);
    }
  }
  const additionalTrackedFiles = options.additionalTrackedFiles ?? [];
  if (!Array.isArray(additionalTrackedFiles) || additionalTrackedFiles.length > 64) fail("INVALID_OPTIONS");
  additionalTrackedFiles.forEach(publicPath);
  let configRoot;
  let state;

  function git(cwd, args, maxBuffer = BOUNDS.gitTreeBytes) {
    const env = { ...process.env };
    for (const key of Object.keys(env)) if (key.startsWith("GIT_")) delete env[key];
    env.GIT_OPTIONAL_LOCKS = "0";
    try {
      return execFileSync("git", [
        "--no-pager", "--no-optional-locks", "--no-replace-objects",
        "-c", "core.fsmonitor=false", "-C", cwd, ...args,
      ], { encoding: null, maxBuffer, timeout: 20_000, windowsHide: true, env, stdio: ["ignore", "pipe", "pipe"] });
    } catch {
      fail("GIT_BINDING_UNAVAILABLE");
    }
  }
  function guardPath(target, directory = false) {
    const relative = relativeTo(state.root, target, true);
    if (relative === ".") {
      if (!directory || !fs.lstatSync(state.root).isDirectory()) fail("INVALID_ROOT");
      return;
    }
    let cursor = state.root;
    const parts = relative.split("/");
    for (let index = 0; index < parts.length; index += 1) {
      cursor = path.join(cursor, parts[index]);
      let stat;
      try { stat = fs.lstatSync(cursor); } catch { fail("SOURCE_UNREADABLE", relative); }
      if (stat.isSymbolicLink()) fail("LINK_REFUSED", relative);
      const wantsDirectory = index < parts.length - 1 || directory;
      if (wantsDirectory ? !stat.isDirectory() : !stat.isFile()) fail("NOT_REGULAR", relative);
    }
  }
  function snapshot(target, limit) {
    const resolved = path.resolve(target);
    const relative = relativeTo(state.root, resolved);
    guardPath(resolved);
    const pathBefore = fs.lstatSync(resolved, { bigint: true });
    let fd;
    try {
      fd = fs.openSync(resolved, fs.constants.O_RDONLY |
        (fs.constants.O_NOFOLLOW ?? 0) | (fs.constants.O_NONBLOCK ?? 0));
      const before = fs.fstatSync(fd, { bigint: true });
      if (!before.isFile() || before.size > BigInt(limit) ||
          signature(before) !== signature(pathBefore)) fail("UNSTABLE_SOURCE", relative);
      const buffer = Buffer.alloc(Math.min(limit + 1, Number(before.size) + 1));
      let used = 0;
      while (used < buffer.length) {
        const count = fs.readSync(fd, buffer, used, buffer.length - used, null);
        if (count === 0) break;
        used += count;
      }
      if (used > limit || BigInt(used) !== before.size) fail("SOURCE_SIZE", relative);
      const after = fs.fstatSync(fd, { bigint: true });
      guardPath(resolved);
      const pathAfter = fs.lstatSync(resolved, { bigint: true });
      if (signature(before) !== signature(after) ||
          signature(before) !== signature(pathAfter)) fail("UNSTABLE_SOURCE", relative);
      const bytes = Buffer.from(buffer.subarray(0, used));
      return { path: resolved, publicPath: relative, limit, size: used,
        sha256: sha256(bytes), identity: signature(before), bytes };
    } catch (error) {
      if (String(error?.message).startsWith("FRONTEND_NOTICES_")) throw error;
      fail("SOURCE_UNREADABLE", relative);
    } finally {
      if (fd !== undefined) fs.closeSync(fd);
    }
  }
  function assertSnapshot(previous) {
    const current = snapshot(previous.path, previous.limit);
    if (current.sha256 !== previous.sha256 || current.size !== previous.size ||
        current.identity !== previous.identity) fail("SOURCE_CHANGED", previous.publicPath);
  }
  function json(snapshotValue) {
    try {
      const value = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(snapshotValue.bytes));
      if (!plain(value)) fail("INVALID_JSON", snapshotValue.publicPath);
      return value;
    } catch (error) {
      if (String(error?.message).startsWith("FRONTEND_NOTICES_")) throw error;
      fail("INVALID_JSON", snapshotValue.publicPath);
    }
  }
  function tracked(snapshotValue) {
    const key = snapshotValue.publicPath;
    let gitBlob = null;
    if (fixture) {
      if (fixtureTracked.get(key) !== snapshotValue.sha256) fail("TRACKED_BYTES_MISMATCH", key);
    } else {
      const entry = state.gitTree.get(key);
      if (!entry || !["100644", "100755"].includes(entry.mode)) fail("UNTRACKED_SOURCE", key);
      gitBlob = createHash(state.objectFormat)
        .update("blob " + snapshotValue.size + "\0").update(snapshotValue.bytes).digest("hex");
      if (gitBlob !== entry.oid) fail("TRACKED_BYTES_MISMATCH", key);
    }
    state.tracked.set(key, { snapshot: snapshotValue, gitBlob });
    return gitBlob;
  }
  function head() {
    if (fixture) return fixture.readHead ? fixture.readHead() : fixture.sourceSha;
    const top = fs.realpathSync(git(state.root, ["rev-parse", "--show-toplevel"]).toString("utf8").trim());
    if (top !== state.root) fail("ROOT_CHANGED");
    return git(state.root, ["rev-parse", "--verify", "HEAD^{commit}"]).toString("utf8").trim();
  }
  function assertBinding() {
    if (!state || head() !== state.sourceSha) fail("HEAD_CHANGED");
    for (const record of state.tracked.values()) assertSnapshot(record.snapshot);
  }
  function directoryEntries(directory) {
    guardPath(directory, true);
    const entries = fs.readdirSync(directory, { withFileTypes: true });
    if (entries.length > BOUNDS.directoryEntries) fail("DIRECTORY_BUDGET");
    return entries.sort((a, b) => a.name.localeCompare(b.name, "en"));
  }
  function noticePaths(packageRoot, manifest, declaration) {
    const explicit = explicitLicensePaths(manifest, declaration);
    const files = new Map();
    const put = (relative, grantsText) => {
      publicPath(relative);
      const old = files.get(relative);
      files.set(relative, { relative, grantsText: grantsText || old?.grantsText || false });
      if (files.size > BOUNDS.noticeFiles) fail("NOTICE_FILE_BUDGET");
    };
    for (const relative of explicit) put(relative, true);
    const visit = (directory, prefix, depth) => {
      if (depth > 4) fail("NOTICE_DIRECTORY_DEPTH");
      for (const entry of directoryEntries(directory)) {
        const relative = prefix ? prefix + "/" + entry.name : entry.name;
        publicPath(relative);
        const absolute = path.join(packageRoot, ...relative.split("/"));
        // Do not infer legal notices from source-code or binary filenames.
        if (/\.(?:[cm]?[jt]sx?|map|json|wasm|node|png|jpe?g|gif|svg|ico|woff2?|ttf|zip|tgz|gz)(?:\.|$)/iu.test(entry.name)) continue;
        if (prefix) {
          if (entry.isDirectory()) visit(absolute, relative, depth + 1);
          else put(relative, /^(?:licenses|licences)\//iu.test(prefix + "/") || /^(?:LICEN[CS]E|COPYING)(?:[._-].*)?$/iu.test(entry.name));
        } else if (/^(?:LICEN[CS]E|COPYING|NOTICE|COPYRIGHT)(?:[._-].*)?$/iu.test(entry.name)) {
          if (!entry.isFile()) fail("NOTICE_NOT_REGULAR", relative);
          put(relative, /^(?:LICEN[CS]E|COPYING)(?:[._-].*)?$/iu.test(entry.name));
        } else if (/^(?:licenses|licences|notices)$/iu.test(entry.name)) {
          if (!entry.isDirectory()) fail("NOTICE_NOT_REGULAR", relative);
          visit(absolute, relative, depth + 1);
        }
      }
    };
    visit(packageRoot, "", 0);
    if (![...files.values()].some((item) => item.grantsText)) fail("LICENSE_TEXT_MISSING");
    return [...files.values()].sort((a, b) => a.relative.localeCompare(b.relative, "en"));
  }
  function componentFor(modulePath) {
    const rootRelative = relativeTo(state.root, modulePath);
    const segments = rootRelative.split("/");
    const nm = segments.lastIndexOf("node_modules");
    if (nm < 0) return null;
    const scoped = segments[nm + 1]?.startsWith("@");
    const nameParts = scoped ? segments.slice(nm + 1, nm + 3) : segments.slice(nm + 1, nm + 2);
    if (nameParts.length !== (scoped ? 2 : 1) || nameParts.some((part) => !part) ||
        segments.length <= nm + nameParts.length + 1) fail("ORPHAN_NPM_MODULE");
    const installedName = nameParts.join("/");
    const packageRoot = path.join(state.root, ...segments.slice(0, nm + 1 + nameParts.length));
    const lockKey = relativeTo(state.lockRoot, packageRoot);
    const previous = state.components.get(lockKey);
    if (previous) return previous;
    if (state.components.size >= BOUNDS.components) fail("COMPONENT_BUDGET");
    const entry = state.lock.packages[lockKey];
    if (!plain(entry)) fail("LOCK_PACKAGE_MISSING", lockKey);
    if (entry.link === true) fail("LINK_PACKAGE_UNSUPPORTED", lockKey);
    const metadata = snapshot(path.join(packageRoot, "package.json"), BOUNDS.manifestBytes);
    const manifest = json(metadata);
    state.metadataBytes += metadata.size;
    if (state.metadataBytes > BOUNDS.capturedMetadataBytes) fail("METADATA_TOTAL_BUDGET");
    const name = boundedString(manifest.name, "package.name", 214);
    const version = boundedString(manifest.version, "package.version", 128);
    if (!/^(?:@[a-z0-9_.-]+\/)?[a-z0-9_.-]+$/iu.test(name) ||
        /[\/\\\s]/u.test(version) ||
        name !== (entry.name ?? installedName) || version !== entry.version) {
      fail("LOCK_PACKAGE_MISMATCH", lockKey);
    }
    const resolved = publicSource(entry.resolved);
    const integrity = boundedString(entry.integrity, "integrity", 1024);
    if (!/^(?:sha(?:256|384|512)-[A-Za-z0-9+/]+={0,2})(?: sha(?:256|384|512)-[A-Za-z0-9+/]+={0,2})*$/u.test(integrity)) {
      fail("PACKAGE_INTEGRITY_MISSING", lockKey);
    }
    for (const key of ["dev", "optional", "devOptional"]) {
      if (entry[key] !== undefined && typeof entry[key] !== "boolean") fail("INVALID_METADATA", key);
    }
    const declaration = licenseDeclaration(manifest);
    const declaredType = typeof declaration === "string" ? declaration : declaration?.type;
    if (typeof entry.license === "string" && declaredType &&
        entry.license !== declaredType) fail("LOCK_LICENSE_MISMATCH", lockKey);
    const listing = noticePaths(packageRoot, manifest, declaration);
    const notices = listing.map((item) => {
      const source = snapshot(path.join(packageRoot, ...item.relative.split("/")), BOUNDS.noticeBytes);
      if (!source.size || source.bytes.includes(0) || !/\S/u.test(source.bytes.toString("utf8"))) {
        fail("LICENSE_TEXT_INVALID", item.relative);
      }
      state.noticeBytes += source.size;
      if (state.noticeBytes > BOUNDS.capturedNoticeBytes) fail("NOTICE_CAPTURE_BUDGET");
      return { ...item, source };
    });
    const component = {
      id: "npm:" + name + "@" + version + "#" + sha256(Buffer.from(lockKey + "\0" + resolved + "\0" + integrity)).slice(0, 16),
      name, version, packageRoot, lockKey, manifest, metadata, declaration,
      resolved, integrity, listing, notices,
      lockClassification: { dev: entry.dev === true, optional: entry.optional === true, devOptional: entry.devOptional === true },
    };
    state.components.set(lockKey, component);
    return component;
  }
  function physicalModulePath(id) {
    let candidate = id;
    if (candidate.startsWith("file:")) {
      try { candidate = fileURLToPath(candidate); } catch { fail("INVALID_MODULE_ID"); }
    } else {
      candidate = candidate.split(/[?#]/u, 1)[0];
      if (candidate.startsWith("/@fs/")) candidate = candidate.slice(5);
    }
    if (!path.isAbsolute(candidate)) fail("ORPHAN_MODULE_ID");
    const absolute = path.resolve(candidate);
    relativeTo(state.root, absolute);
    return absolute;
  }
  function observe(info) {
    if (!state || typeof info?.id !== "string") fail("BUILD_NOT_STARTED");
    if (state.observations.size >= BOUNDS.modules && !state.observations.has(info.id)) fail("MODULE_BUDGET");
    try {
      if (info.isExternal || typeof info.code !== "string") fail("UNOBSERVABLE_MODULE");
      const transformed = Buffer.from(info.code, "utf8");
      if (transformed.length > BOUNDS.moduleBytes) fail("MODULE_BUDGET");
      const record = { id: info.id, idSha256: sha256(Buffer.from(info.id)),
        transformedSha256: sha256(transformed), transformedBytes: transformed.length };
      if (info.id.startsWith("\0") || /^(?:virtual:|vite:|__vite-browser-external:)/u.test(info.id)) {
        record.kind = "virtual";
      } else {
        const modulePath = physicalModulePath(info.id);
        record.raw = snapshot(modulePath, BOUNDS.moduleBytes);
        record.component = componentFor(modulePath);
        record.kind = record.component ? "npm" : "local";
        if (!record.component) record.gitBlob = tracked(record.raw);
        // Raw module bytes are hashed, not retained as a second source tree.
        record.raw.bytes = undefined;
      }
      const previous = state.observations.get(info.id);
      if (previous && (previous.error || previous.transformedSha256 !== record.transformedSha256 ||
          previous.raw?.sha256 !== record.raw?.sha256 || previous.raw?.identity !== record.raw?.identity)) {
        fail("MODULE_CHANGED_DURING_PARSE");
      }
      state.observations.set(info.id, record);
    } catch (error) {
      // Tree-shaken code is not part of the emitted closure. Keep errors so an
      // emitted module can never be reclassified or silently omitted later.
      state.observations.set(info.id, { error });
    }
  }
  function verifyComponent(component) {
    assertSnapshot(component.metadata);
    const listing = noticePaths(component.packageRoot, component.manifest, component.declaration);
    if (canonical(listing) !== canonical(component.listing)) fail("NOTICE_SET_CHANGED", component.lockKey);
    for (const notice of component.notices) assertSnapshot(notice.source);
  }
  function verifyEmitted(context, selected, components) {
    assertBinding();
    for (const record of selected.values()) {
      const current = context.getModuleInfo(record.id);
      if (!current || typeof current.code !== "string" ||
          sha256(Buffer.from(current.code, "utf8")) !== record.transformedSha256) {
        fail("TRANSFORMED_CODE_CHANGED");
      }
      if (record.raw) assertSnapshot(record.raw);
    }
    for (const component of components.values()) verifyComponent(component);
    if (head() !== state.sourceSha) fail("HEAD_CHANGED");
  }

  function outputDirectoryAnchors(directory, mustExist) {
    const relative = relativeTo(state.root, directory, true);
    const paths = [state.root];
    if (relative !== ".") {
      let cursor = state.root;
      for (const part of relative.split("/")) {
        cursor = path.join(cursor, part);
        paths.push(cursor);
      }
    }
    const anchors = [];
    for (const filename of paths) {
      let stat;
      try { stat = fs.lstatSync(filename, { bigint: true }); }
      catch (error) {
        if (!mustExist && error?.code === "ENOENT") break;
        fail("OUTPUT_DIRECTORY_UNREADABLE");
      }
      if (stat.isSymbolicLink() || !stat.isDirectory()) fail("UNSAFE_OUTPUT_DIRECTORY");
      anchors.push({ path: filename, identity: String(stat.dev) + ":" + String(stat.ino) });
    }
    return anchors;
  }
  function assertOutputAnchors(expected, current) {
    const identities = new Map(current.map((item) => [item.path, item.identity]));
    for (const item of expected) {
      if (identities.get(item.path) !== item.identity) fail("OUTPUT_DIRECTORY_CHANGED");
    }
  }
  function outputDestination(outputOptions, mustExist = false) {
    if (!plain(outputOptions)) fail("INVALID_OUTPUT_OPTIONS");
    const hasDir = outputOptions.dir !== undefined && outputOptions.dir !== null;
    const hasFile = outputOptions.file !== undefined && outputOptions.file !== null;
    if (hasDir && hasFile) fail("AMBIGUOUS_OUTPUT_DESTINATION");
    const format = outputOptions.format ?? null;
    if (format !== null) boundedString(format, "output.format", 32);
    if (!hasDir && !hasFile) {
      if (!fixture) fail("OUTPUT_DESTINATION_REQUIRED");
      return { key: canonical({ fixture_memory: true, format }), directory: null, anchors: [] };
    }
    const value = hasDir ? outputOptions.dir : outputOptions.file;
    boundedString(value, "output.destination", 4096);
    const absolute = path.resolve(value);
    const directory = hasDir ? absolute : path.dirname(absolute);
    const directoryPath = relativeTo(state.root, directory, true);
    const filePath = hasFile ? relativeTo(state.root, absolute) : null;
    const anchors = outputDirectoryAnchors(directory, mustExist);
    return {
      key: canonical({ directory: directoryPath, file: filePath, format }),
      directory,
      directoryKey: directoryPath.toLowerCase(),
      directoryIdentity: anchors.find((item) => item.path === directory)?.identity ?? null,
      anchors,
    };
  }
  function assertUniqueOutputDestination(destination) {
    for (const { destination: previous } of state.outputChecks) {
      const sameDirectory = destination.directory !== null && previous.directory !== null &&
        (destination.directoryKey === previous.directoryKey ||
          (destination.directoryIdentity !== null &&
            destination.directoryIdentity === previous.directoryIdentity));
      // All outputs in a directory share the notice/index namespace, even when
      // output.file or format differs. Case-only directories are conservative conflicts.
      if (destination.key === previous.key || sameDirectory) fail("DUPLICATE_OUTPUT_DESTINATION");
    }
  }

  function generate(context, outputOptions, bundle) {
    if (!state) fail("BUILD_NOT_STARTED");
    const destination = outputDestination(outputOptions);
    const selected = new Map();
    const components = new Map();
    const outputs = [];
    const names = new Set();
    let outputBytes = 0;
    for (const [key, item] of Object.entries(bundle).sort(([a], [b]) => a.localeCompare(b, "en"))) {
      publicPath(key);
      if (item.fileName !== key || names.has(key.toLowerCase()) ||
          key.toLowerCase().startsWith(PREFIX.toLowerCase())) fail("OUTPUT_COLLISION", key);
      names.add(key.toLowerCase());
      const bytes = bytesOfOutput(item);
      outputBytes += bytes.length;
      if (outputBytes > BOUNDS.outputBytes) fail("OUTPUT_BUDGET");
      const output = { path: key, kind: item.type, bytes: bytes.length, sha256: sha256(bytes) };
      if (item.type === "chunk") {
        if (!plain(item.modules)) fail("INVALID_CHUNK_MODULES", key);
        output.modules = [];
        for (const [id, rendering] of Object.entries(item.modules).sort(([a], [b]) => a.localeCompare(b, "en"))) {
          if (!plain(rendering) || !Number.isSafeInteger(rendering.renderedLength) || rendering.renderedLength < 0) {
            fail("INVALID_RENDERED_LENGTH");
          }
          if (rendering.renderedLength === 0) continue;
          const record = state.observations.get(id);
          if (!record) fail("UNOBSERVED_EMITTED_MODULE");
          if (record.error) throw record.error;
          selected.set(id, record);
          if (record.component) components.set(record.component.lockKey, record.component);
          output.modules.push({ module_id_sha256: record.idSha256, rollup_rendered_length: rendering.renderedLength });
        }
      }
      outputs.push(output);
    }
    verifyEmitted(context, selected, components);
    const texts = new Map();
    let noticeBytes = 0;
    const componentRecords = [...components.values()].sort((a, b) => a.id.localeCompare(b.id, "en")).map((component) => ({
      id: component.id, name: component.name, version: component.version,
      role: "frontend-emitted",
      lock_key: component.lockKey,
      source: { resolved: component.resolved, integrity: component.integrity,
        observation: "installed-source-revalidated", package_archive_authentication: "NOT_VERIFIED" },
      package_metadata: { path: component.metadata.publicPath, bytes: component.metadata.size,
        sha256: component.metadata.sha256, license_declaration: component.declaration,
        explicit_license_files: explicitLicensePaths(component.manifest, component.declaration) },
      lock_classification_informational: component.lockClassification,
      permission_decision: "NOT_VERIFIED",
      notices: component.notices.map((notice) => {
        const asset = PREFIX + "texts/" + notice.source.sha256 + ".txt";
        if (!texts.has(asset)) {
          noticeBytes += notice.source.size;
          if (noticeBytes > BOUNDS.noticeTotalBytes) fail("NOTICE_TOTAL_BUDGET");
          texts.set(asset, Buffer.from(notice.source.bytes));
        }
        return { source_path_in_package: notice.relative, asset, bytes: notice.source.size,
          sha256: notice.source.sha256, evidence_kind: notice.grantsText ? "license-text" : "notice-text" };
      }),
    }));
    const moduleRecords = [...selected.values()].sort((a, b) => a.idSha256.localeCompare(b.idSha256)).map((record) => ({
      module_id_sha256: record.idSha256, kind: record.kind,
      path: record.raw?.publicPath ?? null,
      component_id: record.component?.id ?? null,
      source_path_in_package: record.component ? relativeTo(record.component.packageRoot, record.raw.path) : null,
      raw_source: record.raw ? { bytes: record.raw.size, sha256: record.raw.sha256, git_blob: record.gitBlob ?? null } : null,
      rollup_transformed_code: { bytes: record.transformedBytes, sha256: record.transformedSha256 },
      npm_license_evidence: record.kind === "npm",
      virtual_origin: record.kind === "virtual" ? "NOT_VERIFIED" : null,
    }));
    const index = {
      schema: SCHEMA,
      completion: "OBSERVED_RENDERED_JS_MODULE_CLOSURE_ONLY",
      full_package_inventory: "NOT_VERIFIED",
      non_chunk_asset_dependency_closure: "NOT_VERIFIED",
      redistribution_permission: "NOT_VERIFIED",
      source_binding: {
        mode: fixture ? "EXPLICIT_SYNTHETIC_FIXTURE_NOT_GIT_ATTESTED" : "EXACT_HEAD_AND_TRACKED_BLOBS",
        source_sha: state.sourceSha,
        project_path: relativeTo(state.root, state.project, true),
        tracked_inputs: [...state.tracked.values()].map(({ snapshot: item, gitBlob }) => ({
          path: item.publicPath, bytes: item.size, sha256: item.sha256, git_blob: gitBlob,
        })).sort((a, b) => a.path.localeCompare(b.path, "en")),
      },
      package_lock: { path: state.lockSnapshot.publicPath, bytes: state.lockSnapshot.size,
        sha256: state.lockSnapshot.sha256, lockfile_version: state.lock.lockfileVersion },
      output_format: outputOptions.format ?? null,
      output_snapshot: "generateBundle-post-before-notice-assets",
      original_outputs: outputs,
      modules: moduleRecords,
      components: componentRecords,
      generated_notice_assets: [...texts].map(([asset, bytes]) => ({ path: asset, bytes: bytes.length, sha256: sha256(bytes) })),
      limitations: [
        "Only chunk.modules entries with renderedLength > 0 establish this closure.",
        "Virtual and local module records do not establish npm license coverage.",
        "CSS and other assets have byte digests, not a dependency-license closure.",
        "Raw on-disk source hashes and Rollup transformed-code hashes are distinct.",
        "Lock metadata does not authenticate installed files against registry tarballs.",
        "Hook-boundary readback is not protection against every concurrent or ABA write.",
        "Index excludes itself and generated notice texts from original_outputs to avoid circular hashing.",
        "Cargo, native dependencies, platform packages and redistribution decisions require separate evidence.",
      ],
    };
    const indexBytes = Buffer.from(JSON.stringify(index, null, 2) + "\n", "utf8");
    const generated = new Map([...texts, [INDEX, indexBytes]]);
    for (const filename of generated.keys()) {
      publicPath(filename);
      if (names.has(filename.toLowerCase())) fail("OUTPUT_COLLISION", filename);
      names.add(filename.toLowerCase());
    }
    // Revalidate every input before the first emission; emit captured bytes only.
    verifyEmitted(context, selected, components);
    assertUniqueOutputDestination(destination);
    for (const [fileName, source] of generated) {
      const reference = context.emitFile({ type: "asset", fileName, source });
      if (context.getFileName(reference) !== fileName) fail("EMITTED_NAME_CHANGED");
    }
    verifyEmitted(context, selected, components);
    state.outputChecks.push({ selected, components, destination,
      expected: new Map([
        ...outputs.map((item) => [item.path, {
          type: item.kind, size: item.bytes, sha256: item.sha256,
        }]),
        ...[...generated].map(([name, bytes]) => [name, {
          type: "asset", size: bytes.length, sha256: sha256(bytes),
        }]),
      ]),
    });
  }

  return {
    name: "motionwright-candidate-frontend-notices",
    apply: "build",
    enforce: "post",
    configResolved(config) {
      if (config.command !== "build") fail("BUILD_ONLY");
      configRoot = config.root;
    },
    buildStart() {
      const project = fs.realpathSync(path.resolve(options.projectRoot ?? configRoot ?? process.cwd()));
      if (configRoot && fs.realpathSync(path.resolve(configRoot)) !== project) fail("PROJECT_ROOT_MISMATCH");
      const root = fixture
        ? fs.realpathSync(path.resolve(options.sourceRoot))
        : fs.realpathSync(git(project, ["rev-parse", "--show-toplevel"]).toString("utf8").trim());
      if (options.sourceRoot && fs.realpathSync(path.resolve(options.sourceRoot)) !== root) fail("ROOT_MISMATCH");
      relativeTo(root, project, true);
      state = { root, project, observations: new Map(), components: new Map(), tracked: new Map(),
        gitTree: new Map(), outputChecks: [], noticeBytes: 0, metadataBytes: 0 };
      guardPath(project, true);
      const sourceSha = fixture ? fixture.sourceSha : head();
      if (typeof sourceSha !== "string" || !/^(?:[0-9a-f]{40}|[0-9a-f]{64})$/u.test(sourceSha) ||
          (options.sourceSha !== undefined && options.sourceSha !== sourceSha)) fail("SOURCE_SHA_MISMATCH");
      state.sourceSha = sourceSha;
      if (!fixture) {
        state.objectFormat = git(root, ["rev-parse", "--show-object-format"]).toString("utf8").trim();
        if (!["sha1", "sha256"].includes(state.objectFormat)) fail("GIT_OBJECT_FORMAT");
        const tree = git(root, ["ls-tree", "-r", "-z", "--full-tree", sourceSha]).toString("utf8");
        for (const line of tree.split("\0").filter(Boolean)) {
          const match = /^([0-7]+) blob ([0-9a-f]+)\t(.+)$/u.exec(line);
          if (match) state.gitTree.set(match[3], { mode: match[1], oid: match[2] });
        }
      }
      const manifestSnapshot = snapshot(path.join(project, "package.json"), BOUNDS.manifestBytes);
      tracked(manifestSnapshot);
      const manifest = json(manifestSnapshot);
      const lockPath = options.packageLockPath
        ? path.resolve(root, options.packageLockPath)
        : path.join(project, "package-lock.json");
      state.lockRoot = path.dirname(lockPath);
      relativeTo(root, state.lockRoot, true);
      const projectLockKey = relativeTo(state.lockRoot, project, true);
      state.lockSnapshot = snapshot(lockPath, BOUNDS.lockBytes);
      tracked(state.lockSnapshot);
      state.lock = json(state.lockSnapshot);
      if (![2, 3].includes(state.lock.lockfileVersion) || !plain(state.lock.packages)) fail("UNSUPPORTED_LOCKFILE");
      const projectEntry = state.lock.packages[projectLockKey === "." ? "" : projectLockKey];
      if (!plain(projectEntry) || projectEntry.link === true ||
          projectEntry.name !== manifest.name || projectEntry.version !== manifest.version) fail("PROJECT_LOCK_MISMATCH");
      for (const key of ["dependencies", "devDependencies", "optionalDependencies", "peerDependencies"]) {
        if (canonical(projectEntry[key] ?? {}) !== canonical(manifest[key] ?? {})) fail("PROJECT_LOCK_MISMATCH", key);
      }
      for (const relative of additionalTrackedFiles) tracked(snapshot(path.join(root, ...relative.split("/")), BOUNDS.lockBytes));
      if (!fixture) tracked(snapshot(fileURLToPath(import.meta.url), BOUNDS.moduleBytes));
      assertBinding();
    },
    shouldTransformCachedModule() { return true; },
    moduleParsed(info) { observe(info); },
    generateBundle: { order: "post", handler(outputOptions, bundle) { generate(this, outputOptions, bundle); } },
    writeBundle: {
      order: "post",
      sequential: true,
      handler(outputOptions, bundle) {
        if (!state) fail("BUILD_NOT_STARTED");
        const destination = outputDestination(outputOptions, true);
        const matches = state.outputChecks.filter((item) => item.destination.key === destination.key);
        if (matches.length !== 1) fail("OUTPUT_SNAPSHOT_MISSING_OR_AMBIGUOUS");
        const check = matches[0];
        assertOutputAnchors(check.destination.anchors, destination.anchors);
        verifyEmitted(this, check.selected, check.components);
        if (Object.keys(bundle).length !== check.expected.size) fail("LATE_OUTPUT_CHANGED");
        for (const [key, expected] of check.expected) {
          if (!own(bundle, key) || bundle[key].fileName !== key || bundle[key].type !== expected.type) {
            fail("LATE_OUTPUT_CHANGED", key);
          }
          const bytes = bytesOfOutput(bundle[key]);
          if (bytes.length !== expected.size || sha256(bytes) !== expected.sha256) {
            fail("LATE_OUTPUT_CHANGED", key);
          }
          if (destination.directory !== null) {
            const written = snapshot(path.join(destination.directory, ...key.split("/")), expected.size);
            if (written.size !== expected.size || written.sha256 !== expected.sha256) {
              fail("PHYSICAL_OUTPUT_CHANGED", key);
            }
          }
        }
        if (destination.directory !== null) {
          assertOutputAnchors(destination.anchors, outputDirectoryAnchors(destination.directory, true));
        }
        verifyEmitted(this, check.selected, check.components);
      },
    },
  };
}
