import { Eye, ShieldCheck } from "lucide-react";
import { useEffect, useState } from "react";
import { modelRequestPreflight } from "./api";
import type {
  DataClass,
  ModelProviderKind,
  ModelRequestPreflight,
  Project,
  Scene,
} from "./types";

const classLabels: Record<DataClass, string> = {
  metadata: "Metadata",
  text: "Text",
  frame: "Frame / image source",
  audio: "Audio source",
  source_code: "Source code",
};

export default function ModelContextInspector({
  project,
  scene,
}: {
  project: Project;
  scene: Scene;
}) {
  const [providerKind, setProviderKind] = useState<ModelProviderKind>("external_agent");
  const [provider, setProvider] = useState("external-agent");
  const [model, setModel] = useState("client-selected");
  const [resourceRef, setResourceRef] = useState("scene:" + scene.id);
  const [classes, setClasses] = useState<DataClass[]>(["metadata", "text"]);
  const [plan, setPlan] = useState<ModelRequestPreflight | null>(null);
  const [preflightError, setPreflightError] = useState<string | null>(null);
  const [reading, setReading] = useState(false);

  useEffect(() => {
    setResourceRef("scene:" + scene.id);
    setPlan(null);
    setPreflightError(null);
  }, [scene.id, project.revision]);

  const resourceOptions = [
    { value: "scene:" + scene.id, label: "Scene · " + scene.name },
    { value: "project:" + project.id, label: "Project · brief + narrative" },
    ...project.assets.map((asset) => ({
      value: "asset:" + asset.id,
      label: "Asset · " + asset.name + " · " + asset.media_type,
    })),
    ...project.audio.voice_tracks.map((track) => ({
      value: "voice-track:" + track.id,
      label: "Voice · " + track.label,
    })),
    ...project.audio.transcript.map((segment, index) => ({
      value: "transcript:" + segment.id,
      label: "Transcript · " + (segment.speaker ?? "segment " + (index + 1)),
    })),
  ];

  const toggleClass = (dataClass: DataClass) => {
    setClasses((current) =>
      current.includes(dataClass)
        ? current.filter((candidate) => candidate !== dataClass)
        : [...current, dataClass],
    );
    setPlan(null);
    setPreflightError(null);
  };

  const inspect = async () => {
    setReading(true);
    setPreflightError(null);
    setPlan(null);
    try {
      const next = await modelRequestPreflight(project, {
        provider_kind: providerKind,
        provider: provider.trim() || "external-agent",
        model: model.trim() || "client-selected",
        resource_refs: [resourceRef],
        data_classes: classes,
        budget: {
          max_calls: 1,
          max_tokens: 8_000,
          max_cost_microunits: providerKind === "remote" ? 100_000 : null,
        },
      });
      setPlan(next);
    } catch (reason) {
      setPreflightError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setReading(false);
    }
  };

  return (
    <section className="model-context-panel" aria-label="Model request boundary">
      <header>
        <div>
          <h3>Model context boundary</h3>
          <p>
            Inspect the exact local scope before any provider transport. This action never
            dispatches network data.
          </p>
        </div>
        <span className="model-network-state">NETWORK OFF</span>
      </header>

      <div className="model-context-controls">
        <label>
          <span>Mode</span>
          <select
            aria-label="Model provider mode"
            value={providerKind}
            onChange={(event) => {
              setProviderKind(event.target.value as ModelProviderKind);
              setPlan(null);
            }}
          >
            <option value="manual">Manual</option>
            <option value="external_agent">External agent</option>
            <option value="local">Local provider</option>
            <option value="remote">Authorized remote</option>
          </select>
        </label>
        <label>
          <span>Provider</span>
          <input
            aria-label="Model provider"
            value={provider}
            onChange={(event) => {
              setProvider(event.target.value);
              setPlan(null);
            }}
          />
        </label>
        <label>
          <span>Model</span>
          <input
            aria-label="Model identifier"
            value={model}
            onChange={(event) => {
              setModel(event.target.value);
              setPlan(null);
            }}
          />
        </label>
        <label className="model-resource-field">
          <span>Exact resource</span>
          <select
            aria-label="Model context resource"
            value={resourceRef}
            onChange={(event) => {
              setResourceRef(event.target.value);
              setPlan(null);
            }}
          >
            {resourceOptions.map((option) => (
              <option value={option.value} key={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </label>
      </div>

      <div className="model-data-classes" aria-label="Outbound data classes">
        {(Object.keys(classLabels) as DataClass[]).map((dataClass) => (
          <label key={dataClass}>
            <input
              type="checkbox"
              checked={classes.includes(dataClass)}
              onChange={() => toggleClass(dataClass)}
            />
            <span>{classLabels[dataClass]}</span>
          </label>
        ))}
      </div>

      <div className="model-preflight-action">
        <div>
          <strong>One provider only.</strong>
          <span>
            No silent fallback. Source data requires its own explicit consent before a future
            remote dispatch.
          </span>
        </div>
        <button
          type="button"
          className="button compact"
          disabled={reading || classes.length === 0}
          onClick={inspect}
        >
          <Eye size={13} />
          {reading ? "Inspecting…" : "Inspect request boundary"}
        </button>
      </div>

      {preflightError && (
        <div className="model-preflight-error" role="alert">
          {preflightError}
        </div>
      )}

      {plan && (
        <div className="model-preflight-result" role="region" aria-label="Model request preflight result">
          <div className="model-preflight-summary">
            <span>
              <strong>{plan.disclosures.length}</strong> disclosed row
              {plan.disclosures.length === 1 ? "" : "s"}
            </span>
            <span>
              <strong>{plan.estimated_total_bytes.toLocaleString()}</strong> estimated bytes
            </span>
            <span>
              <strong>r{plan.base_revision}</strong> exact base
            </span>
            <span
              className={
                plan.explicit_source_consent_required
                  ? "consent-required"
                  : "consent-not-required"
              }
            >
              {plan.explicit_source_consent_required
                ? "SOURCE CONSENT REQUIRED"
                : "NO SOURCE UPLOAD IMPLIED"}
            </span>
          </div>

          <div className="model-disclosure-list">
            {plan.disclosures.map((row) => (
              <div
                className="model-disclosure-row"
                key={row.resource_ref + ":" + row.data_class}
              >
                <ShieldCheck size={13} aria-hidden="true" />
                <div>
                  <strong>{row.label}</strong>
                  <span>
                    {classLabels[row.data_class]} · {row.estimated_bytes.toLocaleString()} bytes
                    {row.media_type ? " · " + row.media_type : ""}
                  </span>
                  {row.preview && (
                    <code>
                      {row.preview}
                      {row.preview_truncated ? "…" : ""}
                    </code>
                  )}
                </div>
                <span className="untrusted-data-label">UNTRUSTED DATA</span>
              </div>
            ))}
          </div>

          <footer>
            <span className="mono" title={plan.fingerprint_sha256}>
              plan {plan.fingerprint_sha256.slice(0, 12)}…
            </span>
            <span>fallback: {plan.fallback_provider ?? "none"}</span>
            <span>network dispatched: {plan.network_dispatched ? "yes" : "no"}</span>
          </footer>
        </div>
      )}
    </section>
  );
}
