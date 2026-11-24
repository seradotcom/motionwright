/** Synthetic IPC tests: no native grant, provider, render or physical approval. */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fixtureProject } from "./fixture";
import type { HyperframesSourceSnapshot } from "./hyperframesSourceApi";
import { dispatchHyperframesRender, prepareHyperframesRender, readHyperframesRenderIntents,
  readOriginalHyperframesRender, observeOriginalHyperframesJob, parseHyperframesRenderResponse, type HyperframesRenderIntent,
  type HyperframesRenderResponse } from "./hyperframesRenderApi";
const { invokeMock } = vi.hoisted(() => ({ invokeMock: vi.fn() }));
vi.mock("@tauri-apps/api/core", () => ({ invoke: invokeMock }));
const grant = "cccccccc-3333-4333-8333-cccccccccccc";
const project = () => structuredClone(fixtureProject);
function source(p = project()): HyperframesSourceSnapshot {
  return { schema:"motionwright.hyperframes-source/1", project:structuredClone(p), deliverable_id:p.deliverables[0].id,
    fingerprint:"c".repeat(64), source_sha256:"a".repeat(64), source_bytes:"300", html_sha256:"b".repeat(64), html_bytes:"600",
    compiler:"semwright-hyperframes-rect-clips-v3", hyperframes_version:"0.8.181", width:p.deliverables[0].width,
    height:p.deliverables[0].height, frame_count:60, project_modified:false, editor_launched:false, native_execution_started:false };
}
function response(intent: HyperframesRenderIntent): HyperframesRenderResponse {
  return { attempt: { schema:"motionwright.hyperframes-desktop-attempt/1", request_id:intent.request_id,
    source:intent.source, fingerprint:intent.fingerprint, runtime_lock_sha256:intent.runtime_lock_sha256,
    state:"completed", source_current:true, job_ref:"hf-"+"9".repeat(64), frame_count:60, project_modified:false, automatic_retry:false },
    preview:null, preview_problem:null };
}
beforeEach(() => {
  invokeMock.mockReset();
  vi.stubGlobal("window", { __TAURI_INTERNALS__: {} });
  const values = new Map<string, string>();
  vi.stubGlobal("localStorage", {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => { values.set(key, value); },
  });
});
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });
describe("original HyperFrames render custody", () => {
  it("retains the exact original before one scoped grant and one Native command", async () => {
    const p = project(), s = source(p), intent = prepareHyperframesRender(p, s, "d".repeat(64));
    invokeMock.mockImplementation(async (command: string) => {
      expect(readHyperframesRenderIntents(p)[0].request_id).toBe(intent.request_id);
      if (command === "issue_effect_grant") {
        expect(readHyperframesRenderIntents(p)[0].retention).toBe("prepared");
        return { token: grant };
      }
      expect(command).toBe("render_hyperframes_source");
      expect(readHyperframesRenderIntents(p)[0].retention).toBe("dispatched");
      return response(intent);
    });
    const guard = vi.fn();
    await expect(dispatchHyperframesRender(p, s, intent, guard)).resolves.toEqual(response(intent));
    expect(guard).toHaveBeenCalledTimes(2);
    expect(invokeMock.mock.calls[0]).toEqual(["issue_effect_grant", { request: {
      effect:"render_local", project_id:p.id, generation:p.generation, revision:p.revision,
      subject:["hyperframes-render",p.active_branch,s.deliverable_id,s.fingerprint,"d".repeat(64),intent.request_id].join("|"),
    } }]);
    expect(invokeMock.mock.calls).toHaveLength(2);
    expect(readHyperframesRenderIntents(p)[0].retention).toBe("completed");
  });
  it("keeps a lost original acknowledgement through reopen and only reads its old request", async () => {
    const p = project(), s = source(p), intent = prepareHyperframesRender(p, s, "d".repeat(64));
    invokeMock.mockResolvedValueOnce({ token:grant }).mockRejectedValueOnce(new Error("original reply lost"));
    await expect(dispatchHyperframesRender(p, s, intent, () => {})).rejects.toThrow("lost");
    expect(readHyperframesRenderIntents(p)[0].retention).toBe("dispatched");
    expect(() => prepareHyperframesRender(p, s, "d".repeat(64))).toThrow("unconfirmed");
    invokeMock.mockResolvedValueOnce(response(intent));
    const reopened = readHyperframesRenderIntents(p)[0];
    await readOriginalHyperframesRender(p, reopened);
    expect(invokeMock.mock.calls[2]).toEqual(["read_original_hyperframes_render", { request: {
      project_id:p.id, generation:p.generation, request_id:intent.request_id,
    } }]);
    expect(invokeMock.mock.calls.filter(call => call[0] === "render_hyperframes_source")).toHaveLength(1);
    const next = prepareHyperframesRender(p, s, "d".repeat(64));
    expect(next.request_id).not.toBe(intent.request_id);
  });
  it("never treats a missing receipt as permission to repeat an unknown dispatch", async () => {
    const p = project(), s = source(p), intent = prepareHyperframesRender(p, s, "d".repeat(64));
    invokeMock.mockResolvedValueOnce({ token:grant }).mockRejectedValueOnce(new Error("lost"));
    await expect(dispatchHyperframesRender(p, s, intent, () => {})).rejects.toThrow();
    const missing = response(intent);
    Object.assign(missing.attempt, { state:"not_found", source:null, fingerprint:null, runtime_lock_sha256:null,
      source_current:false, job_ref:null, frame_count:null });
    invokeMock.mockResolvedValueOnce(missing);
    await readOriginalHyperframesRender(p, readHyperframesRenderIntents(p)[0]);
    expect(readHyperframesRenderIntents(p)[0].retention).toBe("dispatched");
    expect(() => prepareHyperframesRender(p, s, "d".repeat(64))).toThrow("unconfirmed");
  });
  it("retires a grant response when the source leaves and returns to the same values", async () => {
    const p = project(), s = source(p), intent = prepareHyperframesRender(p, s, "d".repeat(64));
    let epoch = 1;
    invokeMock.mockImplementationOnce(async () => { epoch += 2; return { token:grant }; });
    await expect(dispatchHyperframesRender(p, s, intent, () => {
      if (epoch !== 1) throw new Error("selection retired");
    })).rejects.toThrow("retired");
    expect(invokeMock).toHaveBeenCalledTimes(1);
    expect(readHyperframesRenderIntents(p)[0].retention).toBe("not_sent");
  });
  it("cannot dispatch a second copy while an identical grant request is outstanding", async () => {
    const p = project(), s = source(p), intent = prepareHyperframesRender(p, s, "d".repeat(64));
    const pending: ((value: unknown) => void)[] = [];
    invokeMock.mockImplementation((command: string) => command === "issue_effect_grant"
      ? new Promise(resolve => pending.push(resolve)) : Promise.resolve(response(intent)));
    const first = dispatchHyperframesRender(p, s, intent, () => {});
    const second = dispatchHyperframesRender(p, s, intent, () => {});
    pending[0]({ token:grant }); await first;
    const rejected = expect(second).rejects.toThrow();
    pending[1]({ token:grant }); await rejected;
    expect(invokeMock.mock.calls.filter(call => call[0] === "render_hyperframes_source")).toHaveLength(1);
    expect(readHyperframesRenderIntents(p)[0].retention).toBe("completed");
  });
  it("refuses dispatch when original storage is unavailable", () => {
    const p = project();
    vi.stubGlobal("localStorage", { getItem: () => null, setItem: () => { throw new Error("disk full"); } });
    expect(() => prepareHyperframesRender(p, source(p), "d".repeat(64))).toThrow("disk full");
    expect(invokeMock).not.toHaveBeenCalled();
  });
  it("refuses an unretained request and a stale source before any permission", async () => {
    const p = project(), s = source(p), intent = prepareHyperframesRender(p, s, "d".repeat(64));
    const changed = structuredClone(p); changed.revision++;
    await expect(dispatchHyperframesRender(changed, s, intent, () => {})).rejects.toThrow();
    vi.stubGlobal("localStorage", { getItem: () => null, setItem: () => {} });
    await expect(dispatchHyperframesRender(p, s, intent, () => {})).rejects.toThrow("retained");
    expect(invokeMock).not.toHaveBeenCalled();
  });
  for (const kind of ["request", "source", "runtime", "schema", "retry", "current", "frames", "preview", "unknown_field"] as const) {
    it("rejects a foreign original response: " + kind, () => {
      const p = project(), intent = prepareHyperframesRender(p, source(p), "d".repeat(64));
      const result = response(intent);
      if (kind === "request") result.attempt.request_id = "hf-ui-" + grant;
      if (kind === "source") result.attempt.source = { ...intent.source, active_branch:grant };
      if (kind === "runtime") result.attempt.runtime_lock_sha256 = "e".repeat(64);
      if (kind === "schema") (result.attempt as unknown as Record<string, unknown>).schema = "foreign";
      if (kind === "retry") (result.attempt as unknown as Record<string, unknown>).automatic_retry = true;
      if (kind === "current") p.revision++;
      if (kind === "frames") result.attempt.frame_count = 31;
      if (kind === "preview") result.preview = { token:grant, segment_id:result.attempt.job_ref!, scene_ids:[grant], frame_count:60 };
      if (kind === "unknown_field") (result as unknown as Record<string, unknown>).install_runtime = true;
      expect(() => parseHyperframesRenderResponse(result, p, intent)).toThrow();
    });
  }
  it("provides no browser render or recovery fallback", async () => {
    const p = project(), s = source(p), intent = prepareHyperframesRender(p, s, "d".repeat(64));
    vi.stubGlobal("window", {});
    await expect(dispatchHyperframesRender(p, s, intent, () => {})).rejects.toThrow("Desktop");
    await expect(readOriginalHyperframesRender(p, intent)).rejects.toThrow("Desktop");
    expect(invokeMock).not.toHaveBeenCalled();
  });
});

describe("explicit original job observations", () => {
  it("uses only the original status command and retains running until a terminal result", async () => {
    const p = project(), s = source(p), intent = prepareHyperframesRender(p, s, "d".repeat(64));
    invokeMock.mockResolvedValueOnce({ token:grant }).mockRejectedValueOnce(new Error("lost"));
    await expect(dispatchHyperframesRender(p, s, intent, () => {})).rejects.toThrow("lost");
    const running = response(intent);
    Object.assign(running.attempt, { state:"running", frame_count:null });
    invokeMock.mockResolvedValueOnce(running);
    const guard = vi.fn();
    await expect(observeOriginalHyperframesJob(p, intent, guard)).resolves.toEqual(running);
    expect(guard).toHaveBeenCalledTimes(1);
    expect(invokeMock.mock.calls[2]).toEqual(["observe_original_hyperframes_job", { request:{
      project_id:p.id, generation:p.generation, request_id:intent.request_id,
    } }]);
    expect(readHyperframesRenderIntents(p)[0].retention).toBe("dispatched");
    expect(() => prepareHyperframesRender(p, s, "d".repeat(64))).toThrow("unconfirmed");
    const failed = response(intent);
    Object.assign(failed.attempt, { state:"failed_known", frame_count:null });
    invokeMock.mockResolvedValueOnce(failed);
    await observeOriginalHyperframesJob(p, intent, () => {});
    expect(readHyperframesRenderIntents(p)[0].retention).toBe("failed_known");
    expect(prepareHyperframesRender(p, s, "d".repeat(64)).request_id).not.toBe(intent.request_id);
    expect(invokeMock.mock.calls.filter(call => call[0] === "issue_effect_grant")).toHaveLength(1);
    expect(invokeMock.mock.calls.filter(call => call[0] === "render_hyperframes_source")).toHaveLength(1);
  });
  it("rejects a retired invocation and a foreign original without changing retained custody", async () => {
    const p = project(), s = source(p), intent = prepareHyperframesRender(p, s, "d".repeat(64));
    await expect(observeOriginalHyperframesJob(p, intent, () => { throw new Error("retired owner"); }))
      .rejects.toThrow("retired owner");
    expect(invokeMock).not.toHaveBeenCalled();
    const foreign = response(intent);
    foreign.attempt.request_id = "hf-ui-11111111-1111-4111-8111-111111111111";
    invokeMock.mockResolvedValueOnce(foreign);
    await expect(observeOriginalHyperframesJob(p, intent, () => {})).rejects.toThrow();
    expect(readHyperframesRenderIntents(p)[0].retention).toBe("prepared");
  });
});
