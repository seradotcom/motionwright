import type { CSSProperties } from "react";

/** Closed authored modes. Unknown persisted values never become normal. */
export function canvasBlendStyle(mode: string): NonNullable<CSSProperties["mixBlendMode"]> | null {
  switch (mode) {
    case "normal": return "normal";
    case "multiply": return "multiply";
    case "screen": return "screen";
    case "add": return "plus-lighter";
    default: return null;
  }
}
