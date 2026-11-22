import type { CanvasTransform } from "./types";

/** Editorial rectangular clipping of an axis-aligned child against an authored
 * clip_group. Native Film remains authoritative for rendered pixels and
 * transformed/unsupported clipping is intentionally not approximated. */
export function rectangularClipInset(child: CanvasTransform, parent: CanvasTransform): string | undefined {
  const values = [
    child.x, child.y, child.width, child.height, child.rotation_deg,
    parent.x, parent.y, parent.width, parent.height, parent.rotation_deg,
  ];
  if (values.some(value => !Number.isFinite(value))
    || child.width <= 0 || child.height <= 0
    || parent.width <= 0 || parent.height <= 0
    || child.rotation_deg !== 0 || parent.rotation_deg !== 0) return undefined;
  const fraction = (distance: number, length: number) =>
    Math.min(100, Math.max(0, (distance / length) * 100));
  const top = fraction(parent.y - child.y, child.height);
  const right = fraction(child.x + child.width - parent.x - parent.width, child.width);
  const bottom = fraction(child.y + child.height - parent.y - parent.height, child.height);
  const left = fraction(parent.x - child.x, child.width);
  // CSS clips the actual preview object; it does not conceal/edit the saved
  // source or claim a renderer effect. Native uses Group.clip_intentional.
  return `inset(${top}% ${right}% ${bottom}% ${left}%)`;
}
