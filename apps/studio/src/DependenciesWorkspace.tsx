import { useEffect, useRef, useState } from "react";
import { CircleCheck, CircleDashed, RefreshCw, TriangleAlert } from "lucide-react";
import { assetIntegrityPage } from "./api";
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

/** Render actual app-owned asset/voice associations, never fabricated scene
 * or Graph edges. SHA-256 checks are explicit and read-only in desktop.
 */
export default function DependenciesWorkspace({
  project,
  desktopMode,
}: {
  project: Project;
  desktopMode: boolean;
}) {
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
          <p>
            Real project asset and voice-track references. Project Graph
            admission, cross-renderer consumers and CURRENT/STALE status
            remain unverified.
          </p>
        </div>
        <span className="status-pill status-unknown">GRAPH · UNKNOWN</span>
      </header>
      <section className="asset-audit" aria-label="Local asset SHA-256 integrity">
        <div className="asset-audit-head">
          <div>
            <strong>Local asset integrity</strong>
            <p>Checks exact content-addressed blob bytes already referenced by r{project.revision}.
              No creative edits, renderer jobs, arbitrary filesystem paths or Graph claims.</p>
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
                ? "Not checked · no SHA-256 result claimed"
                : "Local verification is unavailable in browser demo mode"}
            </>
          ) : (
            <>
              {problems > 0 ? <TriangleAlert size={15} aria-hidden="true" />
                : complete && rows.length > 0 && verified === rows.length
                  ? <CircleCheck size={15} aria-hidden="true" />
                  : <CircleDashed size={15} aria-hidden="true" />}
              <span>{rows.length} / {total} assets inspected · {verified} verified ·
                {" "}{problems} integrity problems · {Math.floor(checkedBytes / 1024)} KiB hashed
                {complete ? " · all references examined" : " · more references remain"}
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
          <span>Source asset</span><span>Recorded consumer</span>
          <span>Source revision</span><span>Local integrity</span>
        </div>
        {project.assets.map((asset) => {
          const relatedTracks = project.audio.voice_tracks
            .filter((track) => track.asset_id === asset.id);
          const info = summaries.get(asset.id);
          return (
            <div className="data-row dependency-row" role="row" key={asset.id}
              aria-label={"Asset " + asset.name}>
              <span title={asset.media_type}>{asset.name}</span>
              <span>{relatedTracks.length
                ? relatedTracks.map((track) => "Voice: " + track.label).join(", ")
                : "Not established (Graph needed)"}</span>
              <span>{asset.source_revision ?? "unspecified"}</span>
              <span className={"asset-audit-result " +
                (info && isProblem(info.status) ? "problem" : "")}>
                {info ? descriptions[info.status] : "Not checked"}
                {info?.size_bytes !== null && info?.size_bytes !== undefined
                  ? <small>{" · " + Math.ceil(info.size_bytes / 1024) + " KiB"}</small>
                  : null}
              </span>
            </div>
          );
        })}
      </div>
      {project.assets.length === 0 && (
        <p className="honesty-note">No asset references exist in this project.
          This says nothing about external Graph dependencies.</p>
      )}
      <p className="honesty-note">
        A verified SHA-256 proves only the local bytes for that saved digest at
        inspection time, not usage in a rendered scene, source rights, or Graph admission.
      </p>
    </div>
  );
}
