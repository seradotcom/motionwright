import manifest from "./assets/semantic-graph-fonts/manifest.json";
import type { SemanticGraph } from "./semanticGraph";

export type GraphFontFamily = SemanticGraph["style"]["font_family"];
interface FontAsset {
  path: string; sha256: string; size_bytes: number; codepoint_ranges?: number[][];
}
const assets: readonly FontAsset[] = manifest.files;
const urls: Readonly<Record<string, string>> = {
  "instrument-sans/files/instrument-sans-latin-ext-wght-normal.woff2": new URL("./assets/semantic-graph-fonts/instrument-sans/files/instrument-sans-latin-ext-wght-normal.woff2", import.meta.url).href,
  "instrument-sans/files/instrument-sans-latin-wght-normal.woff2": new URL("./assets/semantic-graph-fonts/instrument-sans/files/instrument-sans-latin-wght-normal.woff2", import.meta.url).href,
  "ibm-plex-mono/files/ibm-plex-mono-cyrillic-ext-400-normal.woff2": new URL("./assets/semantic-graph-fonts/ibm-plex-mono/files/ibm-plex-mono-cyrillic-ext-400-normal.woff2", import.meta.url).href,
  "ibm-plex-mono/files/ibm-plex-mono-cyrillic-400-normal.woff2": new URL("./assets/semantic-graph-fonts/ibm-plex-mono/files/ibm-plex-mono-cyrillic-400-normal.woff2", import.meta.url).href,
  "ibm-plex-mono/files/ibm-plex-mono-vietnamese-400-normal.woff2": new URL("./assets/semantic-graph-fonts/ibm-plex-mono/files/ibm-plex-mono-vietnamese-400-normal.woff2", import.meta.url).href,
  "ibm-plex-mono/files/ibm-plex-mono-latin-ext-400-normal.woff2": new URL("./assets/semantic-graph-fonts/ibm-plex-mono/files/ibm-plex-mono-latin-ext-400-normal.woff2", import.meta.url).href,
  "ibm-plex-mono/files/ibm-plex-mono-latin-400-normal.woff2": new URL("./assets/semantic-graph-fonts/ibm-plex-mono/files/ibm-plex-mono-latin-400-normal.woff2", import.meta.url).href,
};
const families = {
  "Instrument Sans Variable": {
    prefix: "instrument-sans/", alias: "Motionwright Graph Instrument Sans v1",
    license: new URL("./assets/semantic-graph-fonts/instrument-sans/LICENSE.txt", import.meta.url).href,
  },
  "IBM Plex Mono": {
    prefix: "ibm-plex-mono/", alias: "Motionwright Graph IBM Plex Mono v1",
    license: new URL("./assets/semantic-graph-fonts/ibm-plex-mono/LICENSE.txt", import.meta.url).href,
  },
} as const;
const pending = new Map<GraphFontFamily, Promise<void>>();
export const graphPreviewFontFamily = (family: GraphFontFamily): string => families[family].alias;
export const graphPreviewFontLicense = (family: GraphFontFamily): string => families[family].license;

/** Only actual Unicode cmap entries in the unchanged bundled source count.
 * LF separates literal lines in the SVG. Unsupported text stays editable. */
export function graphPreviewMissingCodepoints(family: GraphFontFamily, text: string): string[] {
  const ranges = assets.filter(asset => asset.path.startsWith(families[family].prefix))
    .flatMap(asset => asset.codepoint_ranges ?? []);
  const missing = new Set<number>();
  for (const character of text) {
    const point = character.codePointAt(0)!;
    if (character !== "\n" && !ranges.some(([first, last]) => point >= first && point <= last)) missing.add(point);
  }
  return Array.from(missing, point => "U+" + point.toString(16).toUpperCase().padStart(4, "0"));
}

/** Includes every label that may appear as the playhead moves. Metadata that is
 * not drawn (for example a vertex's meaning) does not constrain the preview. */
export function graphPreviewText(graph: SemanticGraph): string {
  const vertices = graph.content.vertices.map(vertex => vertex.label + (vertex.unit.kind === "symbol" ? " (" + vertex.unit.symbol + ")" : ""));
  const edges = graph.content.edges.map(edge => edge.label + ("influence" in edge
    ? " \u00b7 " + ({ positive: "+", negative: "\u2212", unspecified: "?" }[edge.influence]) : " \u00b7 " + edge.channel));
  const events = graph.content.kind === "system_flow" ? graph.content.events.map(event => event.label) : [];
  const disclosure = graph.content.kind === "system_flow" ? graph.content.source.disclosure
    : graph.content.edges.map(edge => edge.basis.kind === "author_hypothesis" ? "Hypothesis: " + edge.basis.disclosure : "Declared claim: " + edge.basis.claim_id).join("\n");
  return [...vertices, ...edges, ...events, disclosure, "Outside the authored Graph interval"].join("\n");
}

/** Browser editorial typography only. This never establishes native readiness,
 * an authoring context, a save grant or a fidelity result. */
export function loadGraphPreviewFont(family: GraphFontFamily): Promise<void> {
  const existing = pending.get(family); if (existing) return existing;
  const promise = loadFamily(family).catch(reason => { pending.delete(family); throw reason; });
  pending.set(family, promise);
  return promise;
}

async function loadFamily(family: GraphFontFamily): Promise<void> {
  if (typeof FontFace === "undefined" || typeof document === "undefined" || !document.fonts || !globalThis.crypto?.subtle) {
    throw new Error("This browser cannot verify the bundled Graph preview fonts.");
  }
  const selected = families[family], pins = assets.filter(asset => asset.path.startsWith(selected.prefix) && asset.codepoint_ranges);
  const controller = new AbortController(), timeout = setTimeout(() => controller.abort(), 10_000);
  try {
    const faces = await Promise.all(pins.map(async pin => {
      const response = await fetch(urls[pin.path], { signal: controller.signal });
      if (!response.ok) throw new Error("The bundled Graph preview font could not be loaded.");
      const bytes = await response.arrayBuffer();
      if (bytes.byteLength !== pin.size_bytes) throw new Error("The bundled Graph preview font has changed or is incomplete.");
      const observed = Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)), value => value.toString(16).padStart(2, "0")).join("");
      if (observed !== pin.sha256) throw new Error("The bundled Graph preview font does not match its source.");
      const unicodeRange = pin.codepoint_ranges!.map(([first, last]) => "U+" + first.toString(16) + (first === last ? "" : "-" + last.toString(16))).join(",");
      const face = new FontFace(selected.alias, bytes, { style: "normal", weight: "400", unicodeRange });
      return face.load();
    }));
    // Install a complete verified family together; failed loads never leave a
    // partial fallback family in the document.
    for (const face of faces) document.fonts.add(face);
  } catch (reason) {
    controller.abort();
    if (controller.signal.reason?.name === "TimeoutError" || (reason instanceof DOMException && reason.name === "AbortError")) {
      throw new Error("Loading the Graph preview font timed out. The Graph input is preserved.");
    }
    throw reason;
  } finally { clearTimeout(timeout); }
}
