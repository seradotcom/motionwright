/** Selected negative admission tests; no process is spawned or mocked. */
import { describe, expect, it } from "vitest";
import { ScopedChangeService } from "../tests/support/scopedChangeService";

const binaryPath = "E:/source-built/native-graph-review-session.exe";
const databasePath = "E:/original-owner/store/motionwright.sqlite3";

describe("independent Graph development read-only service admission", () => {
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
