/** Pure, deterministic search and keyboard activation for the editor command palette.
 * Commands navigate or control the current view; they do not mutate project documents.
 */
export interface PaletteSearchItem {
  id: string;
  label: string;
  group: string;
  description: string;
  keywords?: string;
  disabled?: boolean;
}

function normalize(value: string): string {
  return value.normalize("NFKD").replace(/[\u0300-\u036f]/g, "").toLocaleLowerCase("en-US");
}

export function filterPaletteCommands<T extends PaletteSearchItem>(
  commands: readonly T[],
  search: string,
): T[] {
  const words = normalize(search).trim().split(/\s+/).filter(Boolean);
  if (words.length === 0) return [...commands];
  return commands.map((item, index) => {
    const label = normalize(item.label);
    const haystack = normalize([item.group, item.label, item.description, item.keywords ?? ""].join(" "));
    if (!words.every((word) => haystack.includes(word))) return null;
    const score = words.reduce(
      (total, word) => total + (label === word ? 8 : label.startsWith(word) ? 5 : label.includes(word) ? 3 : 1),
      0,
    );
    return { item, score, index };
  }).filter((value): value is { item: T; score: number; index: number } => value !== null)
    .sort((a, b) => b.score - a.score || a.index - b.index)
    .map(({ item }) => item);
}

/** Ctrl/Cmd+K and the customary editor Ctrl/Cmd+Shift+P. Never hijack AltGr or IME. */
export function isPaletteShortcut(
  key: Pick<KeyboardEvent, "key" | "ctrlKey" | "metaKey" | "shiftKey" | "altKey" | "isComposing">,
): boolean {
  if (key.isComposing || key.altKey || !(key.ctrlKey || key.metaKey)) return false;
  const lower = key.key.toLowerCase();
  return (lower === "k" && !key.shiftKey) || (lower === "p" && key.shiftKey);
}
