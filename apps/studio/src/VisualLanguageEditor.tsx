import { LockKeyhole, Plus, Trash2, Unlock } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import type {
  Change,
  FontRight,
  MotionVerb,
  Project,
  VisualLanguage,
  VisualToken,
} from "./types";

type Commit = (change: Change) => Promise<void>;

const emptyToken = (): VisualToken => ({ name: "", value: "" });
const emptyMotion = (): MotionVerb => ({
  name: "",
  meaning: "",
  duration_ms: 180,
  reduced_motion: "static_equivalent",
});
const emptyFont = (): FontRight => ({
  family: "",
  source: "",
  rights_status: "unknown",
});

export default function VisualLanguageEditor({
  project,
  commit,
}: {
  project: Project;
  commit: Commit;
}) {
  const language = project.visual_language;
  const [draft, setDraft] = useState<VisualLanguage>(() => structuredClone(language));

  useEffect(() => {
    setDraft(structuredClone(language));
  }, [language]);

  const styleLock = useMemo(
    () =>
      project.locks.find(
        (lock) => lock.resource === "project:" + project.id && lock.kind === "style",
      ) ?? null,
    [project.id, project.locks],
  );
  const dirty = JSON.stringify(draft) !== JSON.stringify(language);
  const valid =
    draft.name.trim().length > 0 &&
    draft.palette.every((token) => token.name.trim() && token.value.trim()) &&
    draft.type_tokens.every((token) => token.name.trim() && token.value.trim()) &&
    draft.motion_grammar.every(
      (verb) =>
        verb.name.trim() &&
        verb.meaning.trim() &&
        Number.isInteger(verb.duration_ms) &&
        verb.duration_ms >= 0 &&
        verb.duration_ms <= 120_000,
    ) &&
    draft.anti_slop_rules.every((rule) => rule.trim().length > 0) &&
    draft.fonts.every((font) => font.family.trim().length > 0);

  const patchToken = (
    collection: "palette" | "type_tokens",
    index: number,
    patch: Partial<VisualToken>,
  ) => {
    setDraft((current) => ({
      ...current,
      [collection]: current[collection].map((token, tokenIndex) =>
        tokenIndex === index ? { ...token, ...patch } : token,
      ),
    }));
  };

  const removeToken = (collection: "palette" | "type_tokens", index: number) => {
    setDraft((current) => ({
      ...current,
      [collection]: current[collection].filter((_, tokenIndex) => tokenIndex !== index),
    }));
  };

  const patchMotion = (index: number, patch: Partial<MotionVerb>) => {
    setDraft((current) => ({
      ...current,
      motion_grammar: current.motion_grammar.map((verb, verbIndex) =>
        verbIndex === index ? { ...verb, ...patch } : verb,
      ),
    }));
  };

  const patchFont = (index: number, patch: Partial<FontRight>) => {
    setDraft((current) => ({
      ...current,
      fonts: current.fonts.map((font, fontIndex) =>
        fontIndex === index ? { ...font, ...patch } : font,
      ),
    }));
  };

  const commitLanguage = async () => {
    if (!dirty || !valid || styleLock || language.version >= 1000) return;
    await commit({
      type: "set_visual_language",
      visual_language: {
        ...structuredClone(draft),
        version: language.version + 1,
        name: draft.name.trim(),
        palette: draft.palette.map((token) => ({
          name: token.name.trim(),
          value: token.value.trim(),
        })),
        type_tokens: draft.type_tokens.map((token) => ({
          name: token.name.trim(),
          value: token.value.trim(),
        })),
        motion_grammar: draft.motion_grammar.map((verb) => ({
          ...verb,
          name: verb.name.trim(),
          meaning: verb.meaning.trim(),
        })),
        anti_slop_rules: draft.anti_slop_rules.map((rule) => rule.trim()),
        fonts: draft.fonts.map((font) => ({
          ...font,
          family: font.family.trim(),
          source: font.source.trim(),
        })),
      },
    });
  };

  return (
    <section className="visual-language-editor" aria-label="Visual language">
      <div className="visual-language-head">
        <div>
          <span className="canvas-panel-heading inline-heading">Visual system</span>
          <strong>{language.name}</strong>
        </div>
        <span className="revision-chip">v{language.version}</span>
      </div>

      <div className="visual-language-lock">
        <span>
          {styleLock
            ? "Project style is explicitly locked."
            : "Edits create a new visual-language version."}
        </span>
        <button
          type="button"
          className={"button compact" + (styleLock ? " warning" : "")}
          onClick={() =>
            styleLock
              ? commit({ type: "remove_lock", lock_id: styleLock.id })
              : commit({
                  type: "set_lock",
                  resource: "project:" + project.id,
                  kind: "style",
                  note: "Approved visual language",
                })
          }
        >
          {styleLock ? <Unlock size={12} /> : <LockKeyhole size={12} />}
          {styleLock ? "Unlock style" : "Protect style"}
        </button>
      </div>

      <label className="visual-field">
        <span className="field-label">System name</span>
        <input
          aria-label="Visual system name"
          value={draft.name}
          disabled={Boolean(styleLock)}
          maxLength={256}
          onChange={(event) => setDraft((current) => ({ ...current, name: event.target.value }))}
        />
      </label>

      <TokenSection
        title="Palette tokens"
        collection={draft.palette}
        disabled={Boolean(styleLock)}
        onPatch={(index, patch) => patchToken("palette", index, patch)}
        onRemove={(index) => removeToken("palette", index)}
        onAdd={() =>
          setDraft((current) => ({
            ...current,
            palette: [...current.palette, emptyToken()],
          }))
        }
      />

      <TokenSection
        title="Type tokens"
        collection={draft.type_tokens}
        disabled={Boolean(styleLock)}
        onPatch={(index, patch) => patchToken("type_tokens", index, patch)}
        onRemove={(index) => removeToken("type_tokens", index)}
        onAdd={() =>
          setDraft((current) => ({
            ...current,
            type_tokens: [...current.type_tokens, emptyToken()],
          }))
        }
      />

      <div className="visual-editor-section">
        <div className="visual-section-title">
          <span>Motion grammar</span>
          <button
            type="button"
            className="plain-icon"
            aria-label="Add motion verb"
            disabled={Boolean(styleLock)}
            onClick={() =>
              setDraft((current) => ({
                ...current,
                motion_grammar: [...current.motion_grammar, emptyMotion()],
              }))
            }
          >
            <Plus size={13} />
          </button>
        </div>
        <div className="visual-list">
          {draft.motion_grammar.map((verb, index) => (
            <div className="motion-row" key={index}>
              <input
                aria-label={"Motion verb name " + (index + 1)}
                placeholder="verb"
                value={verb.name}
                disabled={Boolean(styleLock)}
                onChange={(event) => patchMotion(index, { name: event.target.value })}
              />
              <input
                aria-label={"Motion verb duration " + (index + 1)}
                type="number"
                min="0"
                max="120000"
                step="10"
                value={verb.duration_ms}
                disabled={Boolean(styleLock)}
                onChange={(event) =>
                  patchMotion(index, { duration_ms: Number(event.target.value) })
                }
              />
              <select
                aria-label={"Reduced motion behavior " + (index + 1)}
                value={verb.reduced_motion}
                disabled={Boolean(styleLock)}
                onChange={(event) =>
                  patchMotion(index, {
                    reduced_motion: event.target.value as MotionVerb["reduced_motion"],
                  })
                }
              >
                <option value="instant">Instant</option>
                <option value="fade_only">Fade only</option>
                <option value="static_equivalent">Static equivalent</option>
              </select>
              <button
                type="button"
                className="plain-icon"
                aria-label={"Remove motion verb " + (index + 1)}
                disabled={Boolean(styleLock)}
                onClick={() =>
                  setDraft((current) => ({
                    ...current,
                    motion_grammar: current.motion_grammar.filter(
                      (_, verbIndex) => verbIndex !== index,
                    ),
                  }))
                }
              >
                <Trash2 size={12} />
              </button>
              <input
                className="motion-meaning"
                aria-label={"Motion verb meaning " + (index + 1)}
                placeholder="semantic meaning"
                value={verb.meaning}
                disabled={Boolean(styleLock)}
                onChange={(event) => patchMotion(index, { meaning: event.target.value })}
              />
            </div>
          ))}
          {draft.motion_grammar.length === 0 && (
            <span className="visual-empty">No motion verbs defined.</span>
          )}
        </div>
      </div>

      <div className="visual-editor-section">
        <div className="visual-section-title">
          <span>Anti-slop rules</span>
          <button
            type="button"
            className="plain-icon"
            aria-label="Add anti-slop rule"
            disabled={Boolean(styleLock)}
            onClick={() =>
              setDraft((current) => ({
                ...current,
                anti_slop_rules: [...current.anti_slop_rules, ""],
              }))
            }
          >
            <Plus size={13} />
          </button>
        </div>
        <div className="visual-list">
          {draft.anti_slop_rules.map((rule, index) => (
            <div className="rule-row" key={index}>
              <input
                aria-label={"Anti-slop rule " + (index + 1)}
                value={rule}
                disabled={Boolean(styleLock)}
                onChange={(event) =>
                  setDraft((current) => ({
                    ...current,
                    anti_slop_rules: current.anti_slop_rules.map((item, ruleIndex) =>
                      ruleIndex === index ? event.target.value : item,
                    ),
                  }))
                }
              />
              <button
                type="button"
                className="plain-icon"
                aria-label={"Remove anti-slop rule " + (index + 1)}
                disabled={Boolean(styleLock)}
                onClick={() =>
                  setDraft((current) => ({
                    ...current,
                    anti_slop_rules: current.anti_slop_rules.filter(
                      (_, ruleIndex) => ruleIndex !== index,
                    ),
                  }))
                }
              >
                <Trash2 size={12} />
              </button>
            </div>
          ))}
        </div>
      </div>

      <div className="visual-editor-section">
        <div className="visual-section-title">
          <span>Font rights</span>
          <button
            type="button"
            className="plain-icon"
            aria-label="Add font right"
            disabled={Boolean(styleLock)}
            onClick={() =>
              setDraft((current) => ({
                ...current,
                fonts: [...current.fonts, emptyFont()],
              }))
            }
          >
            <Plus size={13} />
          </button>
        </div>
        <div className="visual-list">
          {draft.fonts.map((font, index) => (
            <div className="font-row" key={index}>
              <input
                aria-label={"Font family " + (index + 1)}
                placeholder="family"
                value={font.family}
                disabled={Boolean(styleLock)}
                onChange={(event) => patchFont(index, { family: event.target.value })}
              />
              <input
                aria-label={"Font source " + (index + 1)}
                placeholder="source / license reference"
                value={font.source}
                disabled={Boolean(styleLock)}
                onChange={(event) => patchFont(index, { source: event.target.value })}
              />
              <select
                aria-label={"Font rights status " + (index + 1)}
                value={font.rights_status}
                disabled={Boolean(styleLock)}
                onChange={(event) =>
                  patchFont(index, {
                    rights_status: event.target.value as FontRight["rights_status"],
                  })
                }
              >
                <option value="unknown">Unknown</option>
                <option value="cleared">Cleared</option>
                <option value="restricted">Restricted</option>
              </select>
              <button
                type="button"
                className="plain-icon"
                aria-label={"Remove font right " + (index + 1)}
                disabled={Boolean(styleLock)}
                onClick={() =>
                  setDraft((current) => ({
                    ...current,
                    fonts: current.fonts.filter((_, fontIndex) => fontIndex !== index),
                  }))
                }
              >
                <Trash2 size={12} />
              </button>
            </div>
          ))}
          {draft.fonts.length === 0 && (
            <span className="visual-empty">No font rights records attached.</span>
          )}
        </div>
      </div>

      <div className="visual-language-actions">
        <button
          type="button"
          className="button"
          disabled={!dirty || Boolean(styleLock)}
          onClick={() => setDraft(structuredClone(language))}
        >
          Reset
        </button>
        <button
          type="button"
          className="button button-primary"
          disabled={!dirty || !valid || Boolean(styleLock) || language.version >= 1000}
          onClick={() => void commitLanguage()}
        >
          Commit visual system
        </button>
      </div>
      {!valid && dirty && (
        <p className="visual-validation" role="status">
          Fill all token, motion, rule and font identifiers before committing.
        </p>
      )}
    </section>
  );
}

function TokenSection({
  title,
  collection,
  disabled,
  onPatch,
  onRemove,
  onAdd,
}: {
  title: string;
  collection: VisualToken[];
  disabled: boolean;
  onPatch: (index: number, patch: Partial<VisualToken>) => void;
  onRemove: (index: number) => void;
  onAdd: () => void;
}) {
  return (
    <div className="visual-editor-section">
      <div className="visual-section-title">
        <span>{title}</span>
        <button
          type="button"
          className="plain-icon"
          aria-label={"Add " + title.toLowerCase()}
          disabled={disabled}
          onClick={onAdd}
        >
          <Plus size={13} />
        </button>
      </div>
      <div className="visual-list">
        {collection.map((token, index) => (
          <div className="token-row" key={index}>
            <input
              aria-label={title + " name " + (index + 1)}
              placeholder="token"
              value={token.name}
              disabled={disabled}
              onChange={(event) => onPatch(index, { name: event.target.value })}
            />
            <input
              aria-label={title + " value " + (index + 1)}
              placeholder="value"
              value={token.value}
              disabled={disabled}
              onChange={(event) => onPatch(index, { value: event.target.value })}
            />
            <button
              type="button"
              className="plain-icon"
              aria-label={"Remove " + title.toLowerCase() + " " + (index + 1)}
              disabled={disabled}
              onClick={() => onRemove(index)}
            >
              <Trash2 size={12} />
            </button>
          </div>
        ))}
        {collection.length === 0 && (
          <span className="visual-empty">No tokens defined.</span>
        )}
      </div>
    </div>
  );
}
