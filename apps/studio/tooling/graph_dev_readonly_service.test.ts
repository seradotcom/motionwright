/** Selected negative admission tests; no process is spawned or mocked. */
import { describe, expect, it } from "vitest";
import { ScopedChangeService, readOnlyServiceCommandAllowed } from "../tests/support/scopedChangeService";

const binaryPath = "E:/source-built/native-graph-review-session.exe";
const databasePath = "E:/original-owner/store/motionwright.sqlite3";

describe("independent Graph development read-only service admission", () => {
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
