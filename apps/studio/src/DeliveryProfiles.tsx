import { CircleCheck, CircleDashed, FileOutput, Plus, Save, Trash2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { exportCaptionSidecar, exportOtio } from "./api";
import type { Change, DeliverableProfile, Project } from "./types";

type Commit = (change: Change) => Promise<void>;

const codecLabel: Record<DeliverableProfile["video_codec"], string> = {
  h264: "H.264",
  hevc: "HEVC",
  prores_422_hq: "ProRes 422 HQ",
  vp9: "VP9",
  av1: "AV1",
};

function freshProfile(index: number): DeliverableProfile {
  return {
    id: crypto.randomUUID(),
    name: "Delivery " + index,
    width: 1920,
    height: 1080,
    language: "en",
    captions: true,
    caption_format: "web_vtt",
    video_codec: "h264",
    audio_codec: "aac",
    audio_sample_rate_hz: 48000,
    brand_profile: null,
    cut_label: null,
  };
}

export default function DeliveryProfiles({
  project,
  commit,
  desktopMode,
}: {
  project: Project;
  commit: Commit;
  desktopMode: boolean;
}) {
  const [selectedId, setSelectedId] = useState(project.deliverables[0]?.id ?? "");
  const selected = project.deliverables.find((profile) => profile.id === selectedId) ?? project.deliverables[0] ?? null;
  const [draft, setDraft] = useState<DeliverableProfile | null>(() => selected ? structuredClone(selected) : null);
  const [sidecarPath, setSidecarPath] = useState("");
  const [otioPath, setOtioPath] = useState("");
  const [otioLosses, setOtioLosses] = useState<string[]>([]);
  const [busy, setBusy] = useState<"save" | "remove" | "caption" | "otio" | "new" | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const current = project.deliverables.find((profile) => profile.id === selectedId) ?? project.deliverables[0] ?? null;
    if (current && current.id !== selectedId) setSelectedId(current.id);
    setDraft(current ? structuredClone(current) : null);
    setSidecarPath("");
    setOtioLosses([]);
  }, [project.revision, selectedId]);

  const unknownTimings = useMemo(
    () => project.audio.transcript.filter((segment) => segment.alignment.kind === "unknown").length,
    [project.audio.transcript],
  );
  const dirty = Boolean(selected && draft && JSON.stringify(selected) !== JSON.stringify(draft));
  const captionReady = Boolean(
    draft?.captions &&
    project.audio.transcript.length > 0 &&
    unknownTimings === 0,
  );

  const run = async (kind: typeof busy, operation: () => Promise<void>) => {
    setBusy(kind);
    setMessage(null);
    setError(null);
    try {
      await operation();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(null);
    }
  };

  const createProfile = () => void run("new", async () => {
    const profile = freshProfile(project.deliverables.length + 1);
    await commit({ type: "upsert_deliverable", profile });
    setSelectedId(profile.id);
  });

  const saveProfile = () => {
    if (!draft) return;
    void run("save", async () => {
      await commit({ type: "upsert_deliverable", profile: draft });
    });
  };

  const removeProfile = () => {
    if (!selected) return;
    void run("remove", async () => {
      await commit({ type: "remove_deliverable", profile_id: selected.id });
    });
  };

  const exportCaptions = () => {
    if (!draft || dirty || !sidecarPath.trim()) return;
    void run("caption", async () => {
      const result = await exportCaptionSidecar(project, draft.id, sidecarPath.trim());
      setMessage("Caption sidecar written · " + result.cue_count + " cues · " + result.path);
    });
  };

  const exportTimeline = () => {
    if (!otioPath.trim()) return;
    void run("otio", async () => {
      const result = await exportOtio(project, otioPath.trim());
      setOtioLosses(result.loss_report);
      setMessage("OTIO cut written · " + result.scene_count + " scenes · " + result.path);
    });
  };

  return (
    <section className="delivery-profile-workbench" aria-label="Delivery profiles">
      <header className="delivery-profile-heading">
        <div>
          <strong>Output profiles</strong>
          <span>Versioned intent for frame, language, codecs and portable captions.</span>
        </div>
        <button type="button" className="button" disabled={busy !== null} onClick={createProfile}>
          <Plus size={14} /> New profile
        </button>
      </header>

      <div className="delivery-profile-layout">
        <div className="delivery-profile-index" role="list" aria-label="Delivery profile index">
          {project.deliverables.map((profile) => (
            <button
              type="button"
              role="listitem"
              key={profile.id}
              className={profile.id === selected?.id ? "delivery-profile-row selected" : "delivery-profile-row"}
              onClick={() => setSelectedId(profile.id)}
            >
              <span className="format-frame" style={{ aspectRatio: profile.width + " / " + profile.height }} />
              <span className="delivery-profile-row-copy">
                <strong>{profile.name}</strong>
                <span>{profile.width} × {profile.height} · {profile.language.toUpperCase()}</span>
              </span>
              <span className="delivery-codec">{codecLabel[profile.video_codec]}</span>
            </button>
          ))}
        </div>

        {draft && selected && (
          <div className="delivery-profile-editor">
            <div className="delivery-profile-editor-head">
              <div>
                <strong>{selected.name}</strong>
                <span className="mono">profile:{selected.id.slice(0, 8)}</span>
              </div>
              <span className={dirty ? "status-pill status-stale" : "status-pill status-current"}>
                {dirty ? "DIRTY" : "CURRENT"}
              </span>
            </div>

            <div className="delivery-field-grid">
              <label className="span-2">
                <span className="field-label">Profile name</span>
                <input value={draft.name} maxLength={160} onChange={(event) => setDraft({ ...draft, name: event.target.value })} />
              </label>
              <label>
                <span className="field-label">Width</span>
                <input type="number" min={1} max={16384} value={draft.width} onChange={(event) => setDraft({ ...draft, width: Number(event.target.value) })} />
              </label>
              <label>
                <span className="field-label">Height</span>
                <input type="number" min={1} max={16384} value={draft.height} onChange={(event) => setDraft({ ...draft, height: Number(event.target.value) })} />
              </label>
              <label>
                <span className="field-label">Language / locale</span>
                <input value={draft.language} maxLength={64} onChange={(event) => setDraft({ ...draft, language: event.target.value })} />
              </label>
              <label>
                <span className="field-label">Video codec</span>
                <select value={draft.video_codec} onChange={(event) => setDraft({ ...draft, video_codec: event.target.value as DeliverableProfile["video_codec"] })}>
                  <option value="h264">H.264</option>
                  <option value="hevc">HEVC</option>
                  <option value="prores_422_hq">ProRes 422 HQ</option>
                  <option value="vp9">VP9</option>
                  <option value="av1">AV1</option>
                </select>
              </label>
              <label>
                <span className="field-label">Audio codec</span>
                <select value={draft.audio_codec} onChange={(event) => setDraft({ ...draft, audio_codec: event.target.value as DeliverableProfile["audio_codec"] })}>
                  <option value="aac">AAC</option>
                  <option value="pcm_s16_le">PCM 16-bit</option>
                  <option value="opus">Opus</option>
                </select>
              </label>
              <label>
                <span className="field-label">Sample rate</span>
                <select value={draft.audio_sample_rate_hz} onChange={(event) => setDraft({ ...draft, audio_sample_rate_hz: Number(event.target.value) })}>
                  <option value={44100}>44.1 kHz</option>
                  <option value={48000}>48 kHz</option>
                  <option value={96000}>96 kHz</option>
                </select>
              </label>
              <label>
                <span className="field-label">Cut label</span>
                <input value={draft.cut_label ?? ""} maxLength={256} placeholder="e.g. social-short" onChange={(event) => setDraft({ ...draft, cut_label: event.target.value.trim() ? event.target.value : null })} />
              </label>
              <label>
                <span className="field-label">Brand profile</span>
                <input value={draft.brand_profile ?? ""} maxLength={256} placeholder="Optional" onChange={(event) => setDraft({ ...draft, brand_profile: event.target.value.trim() ? event.target.value : null })} />
              </label>
            </div>

            <div className="delivery-editor-actions">
              <button type="button" className="button button-primary" disabled={!dirty || busy !== null} onClick={saveProfile}>
                <Save size={14} /> {busy === "save" ? "Saving…" : "Save profile"}
              </button>
              <button type="button" className="button" disabled={project.deliverables.length <= 1 || busy !== null} onClick={removeProfile}>
                <Trash2 size={14} /> Remove
              </button>
            </div>

            <section className="caption-export-panel" aria-label="Caption sidecar export">
              <header>
                <div>
                  <strong>Caption sidecar</strong>
                  <span>Generated only from transcript segments with known timing evidence.</span>
                </div>
                <label className="caption-toggle">
                  <input type="checkbox" checked={draft.captions} onChange={(event) => setDraft({ ...draft, captions: event.target.checked })} />
                  Captions
                </label>
              </header>
              <div className="caption-controls">
                <label>
                  <span className="field-label">Format</span>
                  <select disabled={!draft.captions} value={draft.caption_format} onChange={(event) => {
                    const format = event.target.value as DeliverableProfile["caption_format"];
                    setDraft({ ...draft, caption_format: format });
                    setSidecarPath("");
                  }}>
                    <option value="web_vtt">WebVTT</option>
                    <option value="srt">SubRip (.srt)</option>
                  </select>
                </label>
                <label className="caption-path">
                  <span className="field-label">Destination · absolute path</span>
                  <input
                    value={sidecarPath}
                    disabled={!desktopMode || !draft.captions}
                    placeholder={draft.caption_format === "srt" ? "/absolute/path/captions.srt" : "/absolute/path/captions.vtt"}
                    onChange={(event) => setSidecarPath(event.target.value)}
                  />
                </label>
                <button
                  type="button"
                  className="button"
                  disabled={!desktopMode || !captionReady || dirty || !sidecarPath.trim() || busy !== null}
                  onClick={exportCaptions}
                >
                  <FileOutput size={14} /> {busy === "caption" ? "Writing…" : "Export sidecar"}
                </button>
              </div>
              {!desktopMode && (
                <div className="delivery-truth-note"><CircleDashed size={14} /> Desktop filesystem capability is required.</div>
              )}
              {draft.captions && project.audio.transcript.length === 0 && (
                <div className="delivery-truth-note"><CircleDashed size={14} /> No transcript segments exist; no caption file is implied.</div>
              )}
              {draft.captions && unknownTimings > 0 && (
                <div className="delivery-truth-note warning"><CircleDashed size={14} /> {unknownTimings} transcript segment{unknownTimings === 1 ? "" : "s"} still have UNKNOWN timing.</div>
              )}
              {dirty && <div className="delivery-truth-note">Save the profile before exporting its sidecar.</div>}
            </section>

            <section className="caption-export-panel" aria-label="OpenTimelineIO export">
              <header>
                <div>
                  <strong>OpenTimelineIO interchange</strong>
                  <span>Conservative linear cut only. Unsupported Motionwright semantics are returned as a loss report.</span>
                </div>
                <span className="status-pill status-unknown">LOSS-AWARE</span>
              </header>
              <div className="caption-controls">
                <label className="caption-path">
                  <span className="field-label">Destination · absolute .otio path</span>
                  <input
                    aria-label="OpenTimelineIO export path"
                    value={otioPath}
                    disabled={!desktopMode}
                    placeholder="/absolute/path/project.otio"
                    onChange={(event) => setOtioPath(event.target.value)}
                  />
                </label>
                <button
                  type="button"
                  className="button"
                  disabled={!desktopMode || !otioPath.trim() || busy !== null}
                  onClick={exportTimeline}
                >
                  <FileOutput size={14} /> {busy === "otio" ? "Writing…" : "Export .otio"}
                </button>
              </div>
              {!desktopMode && (
                <div className="delivery-truth-note"><CircleDashed size={14} /> Desktop filesystem capability is required; browser demo does not fabricate an OTIO file.</div>
              )}
              {otioLosses.length > 0 && (
                <div className="otio-loss-report" role="status" aria-label="OTIO loss report">
                  <strong>Loss report · {otioLosses.length}</strong>
                  <ul>{otioLosses.map((loss) => <li key={loss}>{loss}</li>)}</ul>
                </div>
              )}
            </section>

            {error && <div className="portable-message error" role="alert"><strong>Delivery operation blocked.</strong><span>{error}</span></div>}
            {message && <div className="portable-message success" role="status"><CircleCheck size={15} /><span>{message}</span></div>}
          </div>
        )}
      </div>
    </section>
  );
}
