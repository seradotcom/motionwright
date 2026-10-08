import { describe, expect, it } from "vitest";
import { HISTORY_PAGE_SIZE, validateRecentHistoryPage } from "./historyPaging";
import type { ProjectEvent } from "./types";

const event = (revision: number): ProjectEvent => ({
  revision,
  change: { type: "rename_project", title: "Edited r" + revision },
  created_at: "2026-10-08T12:00:00Z",
});

describe("recent-first project event journal pagination", () => {
  it("loads the newest 100 first and checks one extra row for older pages", () => {
    const received = Array.from({ length: 101 }, (_, i) => event(260 - i));
    const page = validateRecentHistoryPage(received, 260);
    expect(page.events).toHaveLength(HISTORY_PAGE_SIZE);
    expect(page.events[0].revision).toBe(260);
    expect(page.events.at(-1)?.revision).toBe(161);
    expect(page.hasEarlier).toBe(true);
    expect(validateRecentHistoryPage(Array.from({ length: 60 }, (_, i) => event(60 - i)), 60))
      .toMatchObject({ hasEarlier: false });
  });

  it("fails closed when rows overlap, are duplicated, out of order or out of bounds", () => {
    expect(() => validateRecentHistoryPage([event(6), event(6)], 6)).toThrow("strictly newest-first");
    expect(() => validateRecentHistoryPage([event(5), event(6)], 6)).toThrow("strictly newest-first");
    expect(() => validateRecentHistoryPage([event(7)], 6)).toThrow("strictly newest-first");
    expect(() => validateRecentHistoryPage([event(0)], 6)).toThrow("strictly newest-first");
    expect(() => validateRecentHistoryPage(Array.from({ length: 102 }, (_, i) => event(200 - i)), 200))
      .toThrow("page size");
  });

  it("supports exact oldest-page exhaustion without an offset", () => {
    const page = validateRecentHistoryPage([event(3), event(2), event(1)], 3);
    expect(page.events.map((record) => record.revision)).toEqual([3, 2, 1]);
    expect(page.hasEarlier).toBe(false);
  });
});
