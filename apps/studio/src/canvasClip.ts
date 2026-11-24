import type { CanvasTransform } from "./types";

/** An exact local-space clip for a world-space rectangular parent.
 * CSS rotates the child around its center; inverse-transforming the parent's
 * rectangle before clipping preserves that same plane for rotated children. */
export function rectangularClipInset(child: CanvasTransform, parent: CanvasTransform): string | undefined {
  const values = [
    child.x, child.y, child.width, child.height, child.rotation_deg,
    parent.x, parent.y, parent.width, parent.height, parent.rotation_deg,
  ];
  if (values.some(value => !Number.isFinite(value))
    || child.width <= 0 || child.height <= 0
    || parent.width <= 0 || parent.height <= 0
    || parent.rotation_deg !== 0) return undefined;
  const fraction = (distance: number, length: number) =>
    Math.min(100, Math.max(0, (distance / length) * 100));
  if (child.rotation_deg % 360 === 0) {
    const top = fraction(parent.y - child.y, child.height);
    const right = fraction(child.x + child.width - parent.x - parent.width, child.width);
    const bottom = fraction(child.y + child.height - parent.y - parent.height, child.height);
    const left = fraction(parent.x - child.x, child.width);
    return `inset(${top}% ${right}% ${bottom}% ${left}%)`;
  }

  type Point = { x: number; y: number };
  const radians = child.rotation_deg * Math.PI / 180;
  const cosine = Math.cos(radians), sine = Math.sin(radians);
  const cx = child.x + child.width / 2, cy = child.y + child.height / 2;
  let polygon: Point[] = [
    { x: 0, y: 0 }, { x: child.width, y: 0 },
    { x: child.width, y: child.height }, { x: 0, y: child.height },
  ].map(point => {
    const x = point.x - child.width / 2, y = point.y - child.height / 2;
    return { x: cx + x * cosine - y * sine, y: cy + x * sine + y * cosine };
  });
  // Intersect with each actual clipping plane. The resulting polygon has at
  // most eight vertices; no image sampling, bounding-box crop or alpha fade.
  for (const [axis, boundary, direction] of [
    ["x", parent.x, 1], ["x", parent.x + parent.width, -1],
    ["y", parent.y, 1], ["y", parent.y + parent.height, -1],
  ] as const) {
    const clipped: Point[] = [];
    for (let index = 0; index < polygon.length; index++) {
      const current = polygon[index], previous = polygon[(index + polygon.length - 1) % polygon.length];
      const currentDistance = direction * (current[axis] - boundary);
      const previousDistance = direction * (previous[axis] - boundary);
      const inside = currentDistance >= 0, wasInside = previousDistance >= 0;
      if (inside !== wasInside) {
        const ratio = previousDistance / (previousDistance - currentDistance);
        clipped.push({
          x: previous.x + (current.x - previous.x) * ratio,
          y: previous.y + (current.y - previous.y) * ratio,
        });
      }
      if (inside) clipped.push(current);
    }
    polygon = clipped;
  }
  if (polygon.length < 3) return "inset(100%)";
  const coordinates = polygon.map(point => {
    const dx = point.x - cx, dy = point.y - cy;
    const localX = dx * cosine + dy * sine + child.width / 2;
    const localY = -dx * sine + dy * cosine + child.height / 2;
    return `${fraction(localX, child.width)}% ${fraction(localY, child.height)}%`;
  });
  return `polygon(${coordinates.join(", ")})`;
}

/** All static rectangular ancestor masks intersect in world coordinates.
 * An empty intersection is an intentional fully clipped result. */
export function intersectRectangularClips(clips: readonly CanvasTransform[]): CanvasTransform | undefined {
  if (!clips.length || clips.some(clip => ![clip.x, clip.y, clip.width, clip.height, clip.rotation_deg].every(Number.isFinite)
    || clip.width <= 0 || clip.height <= 0 || clip.rotation_deg !== 0)) return undefined;
  const x = Math.max(...clips.map(clip => clip.x)), y = Math.max(...clips.map(clip => clip.y));
  const right = Math.min(...clips.map(clip => clip.x + clip.width));
  const bottom = Math.min(...clips.map(clip => clip.y + clip.height));
  return { x, y, width: Math.max(0, right - x), height: Math.max(0, bottom - y), rotation_deg: 0, opacity: 1 };
}

export function rectangularAncestorClip(child: CanvasTransform, clips: readonly CanvasTransform[]): string | undefined {
  const intersection = intersectRectangularClips(clips);
  if (!intersection) return undefined;
  if (intersection.width === 0 || intersection.height === 0) return "inset(100%)";
  return rectangularClipInset(child, intersection);
}
