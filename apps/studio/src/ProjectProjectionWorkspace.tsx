import { useEffect, useState } from "react";
import { ProjectReadSupersededError } from "./projectMutation";
import {
  PROJECT_SCENE_PAGE_SIZE, readProjectionNodesWithinBudget, readProjectionScenesWithinBudget,
  type ProjectionNodeCursor, type ProjectionNodePage, type ProjectionSceneCursor, type ProjectionScenePage,
  type ProjectionSceneSummary, type StudioProjectView,
} from "./projectProjection";

interface Props {
  view: Extract<StudioProjectView, { kind: "projection" }>;
  busy: boolean;
  notice: string | null;
  read: <R>(load: () => Promise<R>, publish: (result: R) => void) => Promise<R>;
  onLibrary: () => void;
  onReload: () => Promise<void>;
  onOpenEditor: () => Promise<void>;
}
function range(label: string, start: number, length: number, total: number): string {
  return label + " " + (length ? start + 1 : 0) + "–" + (start + length) + " of " + total;
}

/** Bounded presentation of saved observations, never a partial editor Project. */
export default function ProjectProjectionWorkspace({ view, busy, notice, read, onLibrary, onReload, onOpenEditor }: Props) {
  const [scenePages, setScenePages] = useState<{ page: ProjectionScenePage; earlier: number[] }>({
    page: view.scenes, earlier: [],
  });
  const [nodePages, setNodePages] = useState<{
    scene: ProjectionSceneSummary; page: ProjectionNodePage; earlier: number[];
  } | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    setScenePages({ page: view.scenes, earlier: [] });
    setNodePages(null);
    setError(null);
  }, [view]);
  const { head } = view;
  const { page: scenes, earlier: earlierScenes } = scenePages;
  const selected = nodePages?.scene;
  const nodes = nodePages?.page;

  async function perform(operation: () => Promise<unknown>): Promise<void> {
    setError(null);
    try { await operation(); }
    catch (reason) {
      if (reason instanceof ProjectReadSupersededError) return;
      setError(reason instanceof Error ? reason.message : "This saved page could not be verified. The current view was preserved.");
    }
  }
  const scenePage = (cursor: ProjectionSceneCursor, earlier: number[], limit?: number) => perform(() => read(
    () => readProjectionScenesWithinBudget(head, cursor, limit),
    page => { setScenePages({ page, earlier }); setNodePages(null); },
  ));
  const nodePage = (scene: ProjectionSceneSummary, cursor: ProjectionNodeCursor | null = null,
    earlier: number[] = [], limit?: number) => perform(() => read(
    () => readProjectionNodesWithinBudget(head, scene, cursor, limit),
    page => setNodePages({ scene, page, earlier }),
  ));
  const loadedNodes = new Set(nodes?.items.map(node => node.id.toLowerCase()) ?? []);
  const reference = (id: string) => id + (loadedNodes.has(id.toLowerCase()) ? " — loaded on this page" : " — not loaded on this page");
  const previousSceneStart = earlierScenes.at(-1);
  const previousNodeStart = nodePages?.earlier.at(-1);

  return (
    <section aria-label="Saved project pages" style={{ padding: "24px", maxWidth: "1440px", margin: "0 auto", width: "100%", boxSizing: "border-box" }}>
      <header style={{ display: "flex", flexWrap: "wrap", justifyContent: "space-between", gap: "20px", alignItems: "start" }}>
        <div>
          <p className="eyebrow">Saved pages</p>
          <h1>{head.title}</h1>
          <p>Revision {head.identity.revision}</p>
          <p>Browse saved scenes and nodes one page at a time. Open the editor to edit the complete project.</p>
        </div>
        <div style={{ display: "flex", flexWrap: "wrap", gap: "8px" }}>
          <button type="button" className="button" disabled={busy} onClick={onLibrary}>Open project library</button>
          <button type="button" className="button" disabled={busy} onClick={() => void perform(onReload)}>Reload saved pages</button>
          <button type="button" className="button button-primary" disabled={busy}
            title="Loads the complete project for editing" onClick={() => void perform(onOpenEditor)}>Open editor</button>
        </div>
      </header>
      {notice && <p role="status">{notice}</p>}
      {busy && <p role="status" aria-live="polite">Reading saved project…</p>}
      {error && <p role="alert">{error}</p>}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 320px), 1fr))", gap: "24px", marginTop: "24px" }}>
        <section aria-label="Saved scenes">
          <h2>{range("Scenes", scenes.start_ordinal, scenes.items.length, scenes.total_scenes)}</h2>
          <p>Up to {PROJECT_SCENE_PAGE_SIZE} scene summaries are shown on each page.</p>
          {!scenes.items.length ? <p>No saved scenes.</p> : (
            <ol start={scenes.start_ordinal + 1} aria-label="Loaded scene summaries">
              {scenes.items.map(scene => <li key={scene.id} style={{ marginBottom: "12px" }}>
                <button type="button" className="button" disabled={busy}
                  aria-label={"View nodes of " + scene.name} aria-pressed={selected?.id === scene.id}
                  onClick={() => void nodePage(scene)}>{scene.name}</button>
                <p>{scene.renderer} · {scene.status} · {scene.beat_count} beats · {scene.node_count} nodes</p>
                <p>Start {scene.start.num}/{scene.start.den} s · Duration {scene.duration.num}/{scene.duration.den} s</p>
              </li>)}
            </ol>
          )}
          <nav aria-label="Scene pages" style={{ display: "flex", gap: "8px" }}>
            <button type="button" className="button" disabled={busy || previousSceneStart === undefined}
              onClick={() => {
                if (previousSceneStart === undefined) return;
                void scenePage({ identity: head.identity, next_ordinal: previousSceneStart },
                  earlierScenes.slice(0, -1), scenes.start_ordinal - previousSceneStart);
              }}>Previous scenes</button>
            <button type="button" className="button" disabled={busy || !scenes.next_cursor}
              onClick={() => {
                if (scenes.next_cursor) void scenePage(scenes.next_cursor, [...earlierScenes, scenes.start_ordinal]);
              }}>Next scenes</button>
          </nav>
        </section>
        <section aria-label="Saved nodes">
          <h2>{selected ? selected.name : "Choose a saved scene"}</h2>
          {!selected || !nodes || !nodePages ? <p>Choose a scene to load its first node page. Other scenes and nodes remain unloaded.</p> : <>
            <h3>{range("Nodes", nodes.start_ordinal, nodes.items.length, nodes.total_nodes)}</h3>
            <p>Only this node page is loaded. References to other nodes are preserved and labelled when they are off the page.</p>
            {!nodes.items.length ? <p>No saved nodes in this scene.</p> : (
              <ol start={nodes.start_ordinal + 1} aria-label="Loaded saved nodes">
                {nodes.items.map(node => <li key={node.id} style={{ marginBottom: "16px", overflowWrap: "anywhere" }}>
                  <strong>{node.name}</strong><span> · {node.kind}</span>
                  <p>{node.id}</p>
                  <p>{node.parent_id === null ? "No saved parent reference." : "Parent: " + reference(node.parent_id)}</p>
                  {node.relations.slice(0, 8).map(relation => <p key={relation.id}>{relation.kind + ": " + reference(relation.target_id)}</p>)}
                  {node.relations.length > 8 && <p>{node.relations.length - 8} more saved relationships in this node are not shown in this preview.</p>}
                </li>)}
              </ol>
            )}
            <nav aria-label="Node pages" style={{ display: "flex", gap: "8px" }}>
              <button type="button" className="button" disabled={busy || previousNodeStart === undefined}
                onClick={() => {
                  if (previousNodeStart === undefined) return;
                  void nodePage(selected, { identity: head.identity, scene: nodes.scene, next_ordinal: previousNodeStart },
                    nodePages.earlier.slice(0, -1), nodes.start_ordinal - previousNodeStart);
                }}>Previous nodes</button>
              <button type="button" className="button" disabled={busy || !nodes.next_cursor}
                onClick={() => {
                  if (nodes.next_cursor) void nodePage(selected, nodes.next_cursor, [...nodePages.earlier, nodes.start_ordinal]);
                }}>Next nodes</button>
            </nav>
          </>}
        </section>
      </div>
    </section>
  );
}
