/** Static, bounded size modulation of the existing editable procedural nodes. */
import type { ProceduralConfig } from "./proceduralField";

export interface ProceduralInfluence {
  center_x: number;
  center_y: number;
  radius: number;
  target_size: number;
}
export interface ProceduralInfluenceRaw {
  enabled: boolean;
  center_x: string;
  center_y: string;
  radius: string;
  target_size: string;
}
const keys = ["center_x", "center_y", "radius", "target_size"] as const;
const record = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);
export function isProceduralInfluenceRaw(value: unknown): value is ProceduralInfluenceRaw {
  return record(value) && Object.keys(value).length === 5 && typeof value.enabled === "boolean"
    && keys.every(key => typeof value[key] === "string");
}
export function rawProceduralInfluence(value: ProceduralInfluence): ProceduralInfluenceRaw {
  return { enabled: true, center_x: String(value.center_x), center_y: String(value.center_y),
    radius: String(value.radius), target_size: String(value.target_size) };
}
export function defaultProceduralInfluenceRaw(config: ProceduralConfig): ProceduralInfluenceRaw {
  return rawProceduralInfluence({
    center_x: config.origin_x + Math.floor(config.area_width / 2),
    center_y: config.origin_y + Math.floor(config.area_height / 2),
    radius: Math.max(1, Math.min(1920, Math.floor(Math.min(config.area_width, config.area_height) / 2))),
    target_size: Math.min(128, config.size * 2),
  });
}
/** Disabled input remains recoverable; only the submitted wire omits its effect. */
export function parseProceduralInfluence(raw: ProceduralInfluenceRaw): ProceduralInfluence | undefined {
  if (!isProceduralInfluenceRaw(raw)) throw new Error("Unreadable influence input. Your saved copy is retained.");
  if (!raw.enabled) return undefined;
  const result = {} as ProceduralInfluence;
  for (const key of keys) {
    if (!/^-?[0-9]+$/.test(raw[key].trim()))
      throw new Error("Influence " + key + " requires a complete integer. Your exact input is retained.");
    const value = Number(raw[key]);
    if (!Number.isSafeInteger(value))
      throw new Error("Influence " + key + " is outside the integer range. Your exact input is retained.");
    result[key] = value;
  }
  return result;
}
export function validateProceduralInfluence(config: ProceduralConfig): void {
  const value: unknown = config.influence;
  if (value === undefined) return;
  if (!record(value) || Object.keys(value).length !== keys.length
    || !keys.every(key => typeof value[key] === "number" && Number.isSafeInteger(value[key])))
    throw new Error("Influence requires exactly four complete integer parameters.");
  const influence = value as unknown as ProceduralInfluence;
  if (influence.center_x < config.origin_x || influence.center_x > config.origin_x + config.area_width
    || influence.center_y < config.origin_y || influence.center_y > config.origin_y + config.area_height)
    throw new Error("Influence center must be inside the authored field.");
  if (influence.radius < 1 || influence.radius > 1920)
    throw new Error("Influence radius must be between 1 and 1920 pixels.");
  if (influence.target_size < 4 || influence.target_size > 128)
    throw new Error("Influence target size must be between 4 and 128 pixels.");
}
export function proceduralGeneratorVersion(config: ProceduralConfig): 1 | 2 {
  return config.influence === undefined ? 1 : 2;
}
/**
 * Sample the unmodified center using doubled integer coordinates. At/outside the
 * radius the node is byte-for-byte unchanged. Division truncates toward zero in
 * both languages, with all intermediate integers below 2^31.
 */
export function proceduralInfluencedGeometry(x: number, y: number, config: ProceduralConfig):
  { x: number; y: number; size: number } {
  const influence = config.influence;
  if (influence === undefined) return { x, y, size: config.size };
  const dx = 2 * x + config.size - 2 * influence.center_x;
  const dy = 2 * y + config.size - 2 * influence.center_y;
  const radiusSquared = (2 * influence.radius) ** 2;
  const distanceSquared = dx * dx + dy * dy;
  if (distanceSquared >= radiusSquared) return { x, y, size: config.size };
  const size = config.size + Math.trunc((influence.target_size - config.size)
    * (radiusSquared - distanceSquared) / radiusSquared);
  const left2 = 2 * x + config.size - size;
  const top2 = 2 * y + config.size - size;
  if (left2 < 2 * config.origin_x || top2 < 2 * config.origin_y
    || left2 + 2 * size > 2 * (config.origin_x + config.area_width)
    || top2 + 2 * size > 2 * (config.origin_y + config.area_height))
    throw new Error("Influenced object exceeds the authored field. Reduce target size or radius, or move the influence center.");
  return { x: left2 / 2, y: top2 / 2, size };
}
