import { describe, expect, it } from "vitest";
import { rectangularClipInset } from "./canvasClip";
import type { CanvasTransform } from "./types";

const geometry = (x: number, y: number, width: number, height: number): CanvasTransform =>
  ({ x, y, width, height, rotation_deg: 0, opacity: 1 });

describe("MW05-E01-02 editorial source-bound rectangular clipping", () => {
  it("clips exactly the pixels extending beyond its saved parent", () => {
    const parent = geometry(510, 290, 900, 500);
    const child = geometry(860, 460, 700, 160);
    const clip = rectangularClipInset(child, parent);
    expect(clip).toBeDefined();
    expect(clip).toMatch(/^inset\(0% 21\.42857\d+% 0% 0%\)$/);
    expect(rectangularClipInset(geometry(600, 380, 100, 100), parent))
      .toBe("inset(0% 0% 0% 0%)");
  });
  it("bounds all four edges, including an object completely outside the clip", () => {
    const parent = geometry(600, 400, 100, 100);
    expect(rectangularClipInset(geometry(550, 350, 200, 200), parent))
      .toBe("inset(25% 25% 25% 25%)");
    expect(rectangularClipInset(geometry(850, 350, 100, 100), parent))
      .toBe("inset(50% 100% 0% 0%)");
  });
  it("does not invent a transformed clipping preview for unsupported geometry", () => {
    const parent = geometry(600, 400, 100, 100);
    const child = geometry(550, 350, 200, 200);
    expect(rectangularClipInset(child, {...parent, rotation_deg: 12})).toBeUndefined();
    expect(rectangularClipInset({...child, rotation_deg: 5}, parent)).toBeUndefined();
    expect(rectangularClipInset({...child, width: 0}, parent)).toBeUndefined();
    expect(rectangularClipInset({...child, x: NaN}, parent)).toBeUndefined();
  });
});
