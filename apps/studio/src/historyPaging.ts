import type { ProjectEvent } from "./types";

export const HISTORY_PAGE_SIZE = 100;

// Reject invalid, overlapping or unsorted provider pages before the UI
// can claim a complete read of committed revisions.
export function validateRecentHistoryPage(
  returned: readonly ProjectEvent[],
  throughRevision: number,
  pageSize = HISTORY_PAGE_SIZE,
): { events: ProjectEvent[]; hasEarlier: boolean } {
  if (!Number.isSafeInteger(throughRevision) || throughRevision < 0) {
    throw new Error("Invalid project history revision cursor.");
  }
  if (!Array.isArray(returned) || returned.length > pageSize + 1 || pageSize < 1 || pageSize > 255) {
    throw new Error("Invalid project history page size.");
  }
  let previous = throughRevision + 1;
  for (const event of returned) {
    if (
      !Number.isSafeInteger(event.revision)
      || event.revision < 1
      || event.revision > throughRevision
      || event.revision >= previous
    ) {
      throw new Error("Project history page is not strictly newest-first.");
    }
    previous = event.revision;
  }
  return {
    events: returned.slice(0, pageSize),
    hasEarlier: returned.length > pageSize,
  };
}
