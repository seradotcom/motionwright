import { fileURLToPath } from "node:url";
import { defineConfig, type PluginOption } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(async ({ command }) => {
  const candidateBuild = process.env.MOTIONWRIGHT_CANDIDATE_BUILD;
  if (candidateBuild !== undefined && candidateBuild !== "0" && candidateBuild !== "1") {
    throw new Error("MOTIONWRIGHT_CANDIDATE_BUILD must be 0 or 1");
  }
  const plugins: PluginOption[] = [react()];
  if (candidateBuild === "1") {
    if (command !== "build") {
      throw new Error("Candidate dependency evidence requires a build");
    }
    const sourceSha = process.env.MOTIONWRIGHT_CANDIDATE_SOURCE_SHA;
    if (!sourceSha || !/^[0-9a-f]{40}$/u.test(sourceSha)) {
      throw new Error("MOTIONWRIGHT_CANDIDATE_SOURCE_SHA must be a full Git SHA");
    }
    const projectRoot = fileURLToPath(new URL(".", import.meta.url));
    const sourceRoot = fileURLToPath(new URL("../../", import.meta.url));
    const pluginUrl = new URL("./tooling/frontend_dependency_notices.mjs", import.meta.url).href;
    const { candidateFrontendNotices } = await import(pluginUrl);
    plugins.push(candidateFrontendNotices({
      sourceRoot, projectRoot, sourceSha,
      additionalTrackedFiles: ["apps/studio/vite.config.ts"],
    }));
  }
  return {
    plugins,
    clearScreen: false,
    server: { strictPort: true, port: 1420 },
    envPrefix: ["VITE_", "TAURI_"],
  };
});
