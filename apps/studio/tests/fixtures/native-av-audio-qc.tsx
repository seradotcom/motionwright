import { StrictMode } from "react";
import { flushSync } from "react-dom";
import { createRoot } from "react-dom/client";
import NativeAvReview, { NativeAudioQcHistory } from "../../src/NativeAvReview";
import { fixtureBootstrap } from "../../src/fixture";

export interface AudioQcFixtureSelection {
  projectId: string;
  generation: string;
  branch: string;
  revision: number;
  exportToken: string;
  deliverableId: string;
  masterSha256: string;
}

export interface AudioQcFixtureCall {
  id: number;
  command: string;
  args: Record<string, unknown> | null;
  settled: boolean;
}

export interface AudioQcComponentHarness {
  selection(): AudioQcFixtureSelection;
  calls(): AudioQcFixtureCall[];
  setSelection(value: AudioQcFixtureSelection): Promise<void>;
  resolve(id: number, response: unknown): Promise<void>;
  reject(id: number, diagnostic: string): Promise<void>;
  unmount(): Promise<void>;
  mount(): Promise<void>;
  settle(): Promise<void>;
}

declare global {
  interface Window {
    __NATIVE_AV_AUDIO_QC__: AudioQcComponentHarness;
  }
}

const host = document.getElementById("native-av-audio-qc-root");
if (!host) throw new Error("Audio QC component fixture root is missing.");
const root = createRoot(host);
let selection: AudioQcFixtureSelection = {
  projectId: fixtureBootstrap.project.id,
  generation: fixtureBootstrap.project.generation,
  branch: fixtureBootstrap.project.active_branch,
  revision: fixtureBootstrap.project.revision,
  exportToken: "synthetic-audio-master-session-A",
  deliverableId: fixtureBootstrap.project.deliverables[0].id,
  masterSha256: "1".repeat(64),
};
let mounted = true;
const calls: AudioQcFixtureCall[] = [];
const replies = new Map<number, {
  resolve(value: unknown): void;
  reject(reason: Error): void;
}>();
const paint = () => new Promise<void>(resolve =>
  requestAnimationFrame(() => requestAnimationFrame(() => resolve())));

// This is only a transport fixture. NativeAvReview imports the actual API and
// public QC reader; no parser, read guard, selector cache or verdict is copied.
// Every command is recorded. Only the four explicit read-only APIs are allowed.
const runtime = window as unknown as {
  __TAURI_INTERNALS__?: {
    invoke(command: string, args?: Record<string, unknown>): Promise<unknown>;
  };
};
if (new URLSearchParams(window.location.search).get("desktop") !== "0") {
  runtime.__TAURI_INTERNALS__ = {
    invoke(command, args) {
      const call: AudioQcFixtureCall = {
        id: calls.length + 1,
        command,
        args: args === undefined ? null : structuredClone(args),
        settled: false,
      };
      calls.push(call);
      if (command === "read_native_av_audio_qc" || command === "native_av_audio_qc_receipt_inventory"
        || command === "recover_native_av_audio_qc") {
        return new Promise<unknown>((resolve, reject) => {
          replies.set(call.id, { resolve, reject });
        });
      }
      call.settled = true;
      if (command === "review_native_av_master") {
        // Do not fabricate media or exercise a decoder just to test numeric QC.
        return Promise.reject(new Error("Synthetic fixture does not supply MP4 bytes."));
      }
      return Promise.reject(new Error("Unexpected synthetic Audio QC command: " + command));
    },
  };
}

function render() {
  const project = {
    ...structuredClone(fixtureBootstrap.project),
    id: selection.projectId,
    generation: selection.generation,
    active_branch: selection.branch,
    revision: selection.revision,
  };
  // Keep the same component instance across identity changes to exercise its
  // own read guard. Unmount/mount below explicitly creates a fresh instance.
  flushSync(() => root.render(mounted ? <StrictMode>
    {new URLSearchParams(window.location.search).get("recovery") === "1"
      ? <NativeAudioQcHistory project={project} deliverableId={selection.deliverableId} />
      : <NativeAvReview project={project} exportToken={selection.exportToken}
      deliverableId={selection.deliverableId} masterSha256={selection.masterSha256}
      reviewOnly />}
  </StrictMode> : null));
}

function take(id: number) {
  const call = calls.find(candidate => candidate.id === id);
  const reply = replies.get(id);
  if (!call || !reply || call.settled) {
    throw new Error("No pending synthetic Audio QC response " + id);
  }
  call.settled = true;
  replies.delete(id);
  return reply;
}

window.__NATIVE_AV_AUDIO_QC__ = {
  selection: () => structuredClone(selection),
  calls: () => structuredClone(calls),
  async setSelection(value) {
    selection = structuredClone(value);
    render();
    await paint();
  },
  async resolve(id, response) {
    take(id).resolve(structuredClone(response));
    await paint();
  },
  async reject(id, diagnostic) {
    take(id).reject(new Error(diagnostic));
    await paint();
  },
  async unmount() { mounted = false; render(); await paint(); },
  async mount() { mounted = true; render(); await paint(); },
  settle: paint,
};
render();
