import { useEffect, useMemo, useRef, useState } from "react";
import { ArrowUpRight, CircleCheck, CircleDashed, RefreshCw, TriangleAlert } from "lucide-react";
import { assetIntegrityPage } from "./api";
import { assetReferences } from "./assetReferences";
import type {
  AssetIntegrityPage, AssetIntegrityRecord, AssetIntegrityStatus, Project,
} from "./types";

const descriptions: Record<AssetIntegrityStatus, string> = {
  verified: "SHA-256 verified",
  missing: "Missing local blob",
  corrupt: "SHA-256 mismatch",
  unsafe: "Unsafe filesystem path",
  unreadable: "Cannot read local blob",
  not_content_addressed: "Not imported / no digest",
  deferred_by_budget: "Not checked within size budget",
};

function isProblem(status: AssetIntegrityStatus): boolean {
  return ["missing", "corrupt", "unsafe", "unreadable"].includes(status);
}

export default function DependenciesWorkspace({
  project,
  desktopMode,
  onOpenScene,
}: {
  project: Project;
  desktopMode: boolean;
  onOpenScene: (sceneId: string) => void;
}) {
  const references = useMemo(() => assetReferences(project), [project]);
  const [rows, setRows] = useState<AssetIntegrityRecord[]>([]);
  const [next, setNext] = useState<number | null>(null);
  const [total, setTotal] = useState<number | null>(null);
  const [complete, setComplete] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [checkedBytes, setCheckedBytes] = useState(0);
  const epochRef = useRef(0);
  const requestRef = useRef(false);
  const sourceStamp = [project.id, project.generation, project.revision].join(":");

  useEffect(() => {
    // Unmount/change of project invalidates in-flight readback.
    return () => { epochRef.current++; };
  }, [sourceStamp]);

  const summaries = new Map(rows.map((row) => [row.asset_id, row]));
  const problems = rows.filter((row) => isProblem(row.status)).length;
  const verified = rows.filter((row) => row.status === "verified").length;

  async function inspect(cursor: number | null) {
    if (!desktopMode || requestRef.current) return;
    requestRef.current = true;
    const epoch = ++epochRef.current;
    setBusy(true);
    setError(null);
    if (cursor === null) {
      setRows([]);
      setTotal(null);
      setNext(null);
      setComplete(false);
      setCheckedBytes(0);
    }
    try {
      const report: AssetIntegrityPage = await assetIntegrityPage(project, cursor, 8);
      if (epoch !== epochRef.current) return;
      if (report.project_id !== project.id
        || report.generation !== project.generation
        || report.revision !== project.revision
        || report.total_assets !== project.assets.length
        || report.complete !== (report.next === null)
        || (report.next !== null && report.next <= (cursor ?? 0))
        || report.items.length > 8
      ) {
        throw new Error("Asset integrity report did not match this project revision.");
      }
      setRows((previous) => cursor === null ? report.items
        : [...previous, ...report.items.filter((item) =>
          !previous.some((seen) => seen.asset_id === item.asset_id))]);
      setTotal(report.total_assets);
      setNext(report.next);
      setComplete(report.complete);
      setCheckedBytes((bytes) => (cursor === null ? 0 : bytes) + report.checked_bytes);
    } catch (reason) {
      if (epoch === epochRef.current) {
        setError(reason instanceof Error ? reason.message : String(reason));
      }
    } finally {
      if (epoch === epochRef.current) setBusy(false);
      requestRef.current = false;
    }
  }

  return (
    <div className="workspace-scroll table-view dependencies-integrity">
      <header className="workspace-heading">
        <div>
          <h2>Dependencies</h2>
          <p>Trace source assets to saved project references and check the local files.</p>
        </div>
        <span className="status-pill status-unknown">Graph status unknown</span>
      </header>
      <section className="asset-audit" aria-label="Local asset SHA-256 integrity">
        <div className="asset-audit-head">
          <div>
            <strong>Local asset integrity</strong>
            <p>Compares local files with their saved SHA-256 digests at revision {project.revision}.</p>
          </div>
          <button className="button compact" type="button" onClick={() => void inspect(null)}
            disabled={!desktopMode || busy}>
            <RefreshCw size={13} aria-hidden="true" />
            {busy ? "Checking…" : total === null ? "Check local assets" : "Restart integrity check"}
          </button>
        </div>
        <div className="asset-audit-summary" role="status">
          {total === null ? (
            <>
              <CircleDashed size={15} aria-hidden="true" />
              {desktopMode
                ? "Not checked"
                : "Local verification is unavailable in browser demo mode"}
            </>
          ) : (
            <>
              {problems > 0 ? <TriangleAlert size={15} aria-hidden="true" />
                : complete && rows.length > 0 && verified === rows.length
                  ? <CircleCheck size={15} aria-hidden="true" />
                  : <CircleDashed size={15} aria-hidden="true" />}
              <span>{rows.length} / {total} assets inspected. {verified} verified, {problems} with
                integrity problems. {Math.floor(checkedBytes / 1024)} KiB hashed.
                {complete ? " Check complete." : " More assets remain."}
              </span>
            </>
          )}
        </div>
        {error && (
          <div className="asset-audit-error" role="alert">
            <TriangleAlert size={16} aria-hidden="true" />
            <span>{error} Earlier results are not a complete current verification.
              Restart the check before making delivery decisions.</span>
          </div>
        )}
        {total !== null && !complete && (
          <div className="asset-audit-more">
            <span>Version-bound SHA-256 results; individual large files can remain unverified.</span>
            <button className="button compact" type="button"
              aria-label="Check next local asset page"
              disabled={busy || next === null || error !== null}
              onClick={() => void inspect(next)}>
              Check next assets
            </button>
          </div>
        )}
      </section>
      <div className="data-table" role="table" aria-label="Project asset dependencies and integrity">
        <div className="data-row dependency-row data-head" role="row">
          <span role="columnheader">Source asset</span><span role="columnheader">Saved references</span>
          <span role="columnheader">Source revision</span><span role="columnheader">Local integrity</span>
        </div>
        {project.assets.map((asset) => {
          const uses = references.get(asset.id) ?? [];
          const info = summaries.get(asset.id);
          return (
            <div className="data-row dependency-row" role="row" key={asset.id}
              aria-label={"Asset " + asset.name}>
              <span role="cell" title={asset.media_type}>{asset.name}</span>
              <div role="cell">
                {uses.length === 0 ? "No saved references" : <ul className="asset-reference-list">
                  {uses.map(({ key, role, label, scene }) => (
                    <li key={key}>
                      <span className="asset-reference-role">{role}</span>
                      {label && <span>{label}</span>}
                      {scene && <button type="button" className="asset-reference-link"
                        aria-label={"Open scene " + scene.name} onClick={() => onOpenScene(scene.id)}>
                        <span>{scene.name}</span><ArrowUpRight size={12} aria-hidden="true" />
                      </button>}
                    </li>
                  ))}
                </ul>}
              </div>
              <span role="cell">{asset.source_revision ?? "Unspecified"}</span>
              <span role="cell" className={"asset-audit-result " +
                (info && isProblem(info.status) ? "problem" : "")}>
                {info ? descriptions[info.status] : "Not checked"}
                {info?.size_bytes !== null && info?.size_bytes !== undefined
                  ? <small>{Math.ceil(info.size_bytes / 1024)} KiB</small>
                  : null}
              </span>
            </div>
          );
        })}
      </div>
      {project.assets.length === 0 && (
        <p className="honesty-note">Import an asset to trace it here.</p>
      )}
    </div>
  );
}
