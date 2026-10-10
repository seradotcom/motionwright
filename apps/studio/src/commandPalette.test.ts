import { describe, expect, it } from "vitest";
import { filterPaletteCommands, isPaletteShortcut, type PaletteSearchItem } from "./commandPalette";

const commands: PaletteSearchItem[] = [
  { id: "brief", label: "Go to Brief", group: "Navigate", description: "Project context" },
  { id: "deliver", label: "Go to Deliver", group: "Navigate", description: "Verify native masters", keywords: "export mp4" },
  { id: "review", label: "Go to Réview", group: "Navigate", description: "Timecode comments" },
  { id: "play", label: "Play preview", group: "Transport", description: "Editorial playback", keywords: "transport start" },
  { id: "unavailable", label: "Open optional encoder", group: "Transport", description: "Provider missing", disabled: true },
];

describe("editor command search", () => {
  it("retains declared order without a query, including unavailable actions", () => {
    expect(filterPaletteCommands(commands, "").map((item) => item.id))
      .toEqual(["brief", "deliver", "review", "play", "unavailable"]);
  });

  it("finds multi-token, case-independent matches across fields", () => {
    expect(filterPaletteCommands(commands, "EXPORT mp4").map((item) => item.id)).toEqual(["deliver"]);
    expect(filterPaletteCommands(commands, "transport start").map((item) => item.id)).toEqual(["play"]);
  });

  it("matches accent-insensitive labels and favors exact title prefixes", () => {
    expect(filterPaletteCommands(commands, "review").map((item) => item.id)).toEqual(["review"]);
    expect(filterPaletteCommands(commands, "play").map((item) => item.id)).toEqual(["play"]);
  });

  it("does not enable disabled commands or invent fuzzy matches", () => {
    const hit = filterPaletteCommands(commands, "optional");
    expect(hit).toHaveLength(1);
    expect(hit[0].disabled).toBe(true);
    expect(filterPaletteCommands(commands, "creative telepathy")).toEqual([]);
  });

  it("never mutates or sorts the caller's entries", () => {
    const snapshot = commands.map((entry) => entry.id);
    filterPaletteCommands(commands, "go to");
    expect(commands.map((entry) => entry.id)).toEqual(snapshot);
  });
});

function key(key: string, modifiers: Partial<Parameters<typeof isPaletteShortcut>[0]> = {}) {
  return isPaletteShortcut({
    key,
    ctrlKey: false,
    metaKey: false,
    altKey: false,
    shiftKey: false,
    isComposing: false,
    ...modifiers,
  });
}

describe("command shortcuts", () => {
  it("accepts Ctrl/Cmd+K or Ctrl/Cmd+Shift+P", () => {
    expect(key("k", { ctrlKey: true })).toBe(true);
    expect(key("K", { metaKey: true })).toBe(true);
    expect(key("P", { ctrlKey: true, shiftKey: true })).toBe(true);
  });

  it("does not intercept editing, AltGr, or IME composition", () => {
    expect(key("k")).toBe(false);
    expect(key("k", { ctrlKey: true, shiftKey: true })).toBe(false);
    expect(key("p", { ctrlKey: true })).toBe(false);
    expect(key("k", { ctrlKey: true, altKey: true })).toBe(false);
    expect(key("k", { metaKey: true, isComposing: true })).toBe(false);
  });
});
