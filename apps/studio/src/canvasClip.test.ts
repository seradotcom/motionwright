import { describe, expect, it } from "vitest";
import { intersectRectangularClips, rectangularAncestorClip, rectangularClipInset } from "./canvasClip";
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
  it.each([-135, -18, 45, 90, 180, 275])("keeps the same clipping plane at %s degrees", angle => {
    const parent = geometry(600, 400, 100, 100);
    const child = { ...geometry(550, 350, 200, 200), rotation_deg: angle };
    const clip = rectangularClipInset(child, parent)!;
    expect(clip).toMatch(/^polygon\(/);
    const vertices = clip.slice(8, -1).split(", ").map(pair =>
      pair.split(" ").map(value => Number(value.replace("%", "")) * 2));
    const contains = (x: number, y: number) => {
      let inside = false;
      for (let i = 0, j = vertices.length - 1; i < vertices.length; j = i++) {
        const [xi, yi] = vertices[i], [xj, yj] = vertices[j];
        if ((yi > y) !== (yj > y) && x < (xj - xi) * (y - yi) / (yj - yi) + xi) inside = !inside;
      }
      return inside;
    };
    const radians = angle * Math.PI / 180;
    for (let x = 7.37; x < 200; x += 13.1) for (let y = 5.91; y < 200; y += 11.3) {
      const worldX = 650 + (x - 100) * Math.cos(radians) - (y - 100) * Math.sin(radians);
      const worldY = 450 + (x - 100) * Math.sin(radians) + (y - 100) * Math.cos(radians);
      expect(contains(x, y), `sample ${x},${y}`).toBe(worldX >= 600 && worldX <= 700 && worldY >= 400 && worldY <= 500);
    }
  });
  it("hides a rotated child outside the plane and rejects invalid parent geometry", () => {
    expect(rectangularClipInset({ ...geometry(900, 800, 80, 50), rotation_deg: 30 }, geometry(600, 400, 100, 100))).toBe("inset(100%)");
  });
  it("does not invent a transformed clipping preview for unsupported geometry", () => {
    const parent = geometry(600, 400, 100, 100);
    const child = geometry(550, 350, 200, 200);
    expect(rectangularClipInset(child, {...parent, rotation_deg: 12})).toBeUndefined();
    expect(rectangularClipInset({...child, rotation_deg: 5}, parent)).toMatch(/^polygon\(/);
    expect(rectangularClipInset({...child, width: 0}, parent)).toBeUndefined();
    expect(rectangularClipInset({...child, x: NaN}, parent)).toBeUndefined();
  });
});

describe("every ancestor clip plane", () => {
  it("intersects nested masks exactly, including disjoint and rotated child geometry", () => {
    const outer=geometry(200,100,600,500), inner=geometry(400,50,600,400);
    const bounds=intersectRectangularClips([outer,inner])!;
    expect(bounds).toEqual(geometry(400,100,400,350));
    const child=geometry(300,50,600,500);
    for (const angle of [0,30,-75,180]) {
      const rotated={...child,rotation_deg:angle};
      expect(rectangularAncestorClip(rotated,[outer,inner]))
        .toBe(rectangularClipInset(rotated,bounds));
    }
    expect(rectangularAncestorClip(child,[outer,geometry(900,800,100,100)])).toBe("inset(100%)");
    expect(rectangularAncestorClip(child,[])).toBeUndefined();
    expect(rectangularAncestorClip(child,[outer,{...inner,rotation_deg:10}])).toBeUndefined();
  });
});
