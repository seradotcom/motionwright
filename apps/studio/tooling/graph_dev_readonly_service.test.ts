/** Selected negative admission tests; no process is spawned or mocked. */
import { describe, expect, it } from "vitest";
import { ScopedChangeService, readOnlyServiceCommandAllowed, checkedOpenedStoreIdentityWitness } from "../tests/support/scopedChangeService";
import { assertOriginalGraphReadOnlyBootstrap } from "./graph_dev_native_owner_port";
import type { Project } from "../src/types";

const binaryPath = "E:/source-built/native-graph-review-session.exe";
const databasePath = "E:/original-owner/store/motionwright.sqlite3";

describe("independent Graph development read-only service admission", () => {
  it("requires a canonical held-SQLite witness rather than a path or untrusted response", () => {
    const witness = "a".repeat(64);
    expect(checkedOpenedStoreIdentityWitness(witness)).toBe(witness);
    for (const untrusted of [
      null, undefined, false, 0, {}, { sha256: witness },
      "", "a".repeat(63), "a".repeat(65), "A".repeat(64),
      " " + witness, witness + "\n", "E:/Projects/motionwright/original.sqlite3",
    ]) {
      expect(() => checkedOpenedStoreIdentityWitness(untrusted))
        .toThrow("Opened SQLite Store identity witness is invalid");
    }
  });

  it("checks the exact real read-only bootstrap shape without granting desktop or mutation authority", () => {
    // Pure parser fixture, NOT a Native owner, credential, Store or physical
    // success. The actual test receives these values only from the reader.
    const project = { id: "prj_0123456789abcdef0123456789abcdef",
      generation: "01234567-89ab-4cde-8f01-234567890abc",
      revision: 4, production_design: { semantic_graphs: [] } } as unknown as Project;
    const bootstrap = {
      test_transport: "service-test-jsonl",
      read_only: true,
      evidence_scope: "real_service_sqlite_cas_read_only; excludes_tauri_installer_native_render",
      native_sdk: { mode: "browser-demo" },
      project_selection: { status: "selected", project_id: project.id },
      project: structuredClone(project),
    };
    expect(() => assertOriginalGraphReadOnlyBootstrap(bootstrap, project)).not.toThrow();
    for (const invalid of [
      { ...bootstrap, read_only: false },
      { ...bootstrap, native_sdk: { mode: "tauri" } },
      { ...bootstrap, evidence_scope: "real_service_sqlite_cas" },
      { ...bootstrap, project_selection: { status: "selected", project_id: "other" } },
      { ...bootstrap, project: { ...project, revision: 5 } },
      { ...bootstrap, project: null },
      { ...bootstrap, test_transport: "browser-demo" },
      null,
    ]) {
      expect(() => assertOriginalGraphReadOnlyBootstrap(invalid, project))
        .toThrow("GRAPH_DEV_INDEPENDENT_READONLY_BOOTSTRAP_NOT_PROVEN");
    }
  });
  it("permits only the exact source-built reader's five original read commands", () => {
    for (const command of ["bootstrap", "get_project", "reopen", "project_history",
      "opened_store_identity_witness"]) {
      expect(readOnlyServiceCommandAllowed(command)).toBe(true);
    }
    // No caller-created mutation, grant, provider or projection command can
    // enter the reader's JSONL channel, even with a crafted command spelling.
    for (const command of ["apply_change", "issue_effect_grant",
      "semantic_graph_authoring_context", "semantic_graph_operation_recovery",
      "prepare_read_only_handoff", "list_projects", "project_projection_head",
      "readOnly", " reOpen", "reopen ", "", "REOPEN"]) {
      expect(readOnlyServiceCommandAllowed(command)).toBe(false);
    }
  });
  it("requires an explicit source-built binary and original absolute Store", async () => {
    await expect(ScopedChangeService.createReadOnly({
      databasePath, seedFixture: false,
    })).rejects.toThrow(/explicit absolute source-built binary/);
    await expect(ScopedChangeService.createReadOnly({
      binaryPath, databasePath: "relative.sqlite3", seedFixture: false,
    })).rejects.toThrow(/existing SQLite path/);
  });

  it("denies synthetic source seeding and desktop-invoke effect routes", async () => {
    await expect(ScopedChangeService.createReadOnly({
      binaryPath, databasePath, seedFixture: true,
    })).rejects.toThrow(/cannot seed a fixture/);
    await expect(ScopedChangeService.createReadOnly({
      binaryPath, databasePath, seedFixture: false, desktopInvokeFixture: true,
    })).rejects.toThrow(/cannot seed a fixture/);
    await expect(ScopedChangeService.createReadOnly({
      binaryPath, databasePath,
    })).rejects.toThrow(/cannot seed a fixture/);
  });
});
