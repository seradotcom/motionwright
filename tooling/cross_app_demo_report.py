#!/usr/bin/env python3
"""Offline, evidence-bound HTML companion for the real native cross-app E2E.

Never generates successful stages: this creates a readable presentation ONLY
after the exact-SHA native acceptance script wrote and independently checked
its three real phase receipts and artifacts.
"""
from __future__ import annotations

import html
import json
from pathlib import Path


def make_report(evidence: Path) -> Path:
    result = json.loads((evidence / "result.json").read_text())
    stop = json.loads((evidence / "stop.json").read_text())
    resume = json.loads((evidence / "resume.json").read_text())
    repeat = json.loads((evidence / "repeat.json").read_text())
    if (result.get("native_cross_app_recovery_e2e") != "PASS"
            or stop.get("blender") != "executed"
            or resume.get("recovery", {}).get("blender") != "reused"
            or resume.get("recovery", {}).get("motion_canvas") != "executed"
            or repeat.get("recovery", {}).get("blender") != "reused"
            or repeat.get("recovery", {}).get("motion_canvas") != "reused"
            or resume.get("blender_reexecution_count") != 0
            or repeat.get("blender_reexecution_count") != 0):
        raise AssertionError("cannot present unverified cross-app recovery as success")
    for name in ("reused-blender.glb", "artifact-manifest.json",
                 "first-frame.png", "middle-frame.png", "last-frame.png"):
        if not (evidence / name).is_file():
            raise AssertionError("missing genuine native evidence: " + name)
    mw = html.escape(result["source_motionwright"])
    sw = html.escape(result["source_semwright"])
    glb = html.escape(result["blender_glb_sha256"])
    frame_count = int(result["motion_canvas_frame_count"])
    manifest = html.escape(result["motion_canvas_manifest_sha256"])
    if frame_count != 60:
        raise AssertionError("the native demo expected 60 rendered frames")
    page = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Semwright × Motionwright — Cross-app recovery proof</title>
  <style>
    :root {{ color-scheme: dark; --bg:#0c0d11; --card:#171920; --card2:#1c1f27;
      --stroke:#333641; --muted:#a0a4b0; --white:#f6f7fa; --mint:#b6edcf; --amber:#ecc890; }}
    * {{ box-sizing:border-box }} html {{ scroll-behavior:smooth }}
    body {{ margin:0;background:var(--bg);color:var(--white);font:15px/1.6
      -apple-system,BlinkMacSystemFont,"Segoe UI",Inter,sans-serif }}
    main {{ max-width:1180px; padding:36px 28px 90px; margin:auto }}
    .brand {{ display:flex;justify-content:space-between;gap:14px;align-items:center;
      letter-spacing:.10em;text-transform:uppercase;font-size:12px;font-weight:700;color:var(--muted) }}
    .pill {{ border:1px solid #456252;border-radius:99px;padding:7px 13px;
      color:var(--mint);font-size:11px;letter-spacing:.04em;text-transform:none }}
    h1 {{ margin:92px 0 20px;font-size:clamp(42px,7vw,96px);letter-spacing:-.06em;
      line-height:.99;font-weight:680 }}
    .accent {{ color:var(--mint) }} .sub {{ max-width:780px; font-size:20px;color:var(--muted);
      line-height:1.55;margin:0 0 55px }}
    .metrics {{ display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin-bottom:54px }}
    .metric {{ border:1px solid var(--stroke);border-radius:12px;padding:26px;
      background:linear-gradient(160deg,var(--card),#111318) }}
    .metric strong {{ display:block; font-size:43px;letter-spacing:-.06em;line-height:1.2 }}
    .metric label {{ display:block;color:var(--muted);font-size:13px;padding-top:10px }}
    .eyebrow {{ color:var(--mint);font-weight:700;text-transform:uppercase;
      letter-spacing:.16em;font-size:11px;margin:44px 0 14px }}
    h2 {{ font-size:clamp(24px,4vw,38px);letter-spacing:-.04em;margin:0 0 22px }}
    .phases {{ display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px }}
    .phase {{ background:var(--card);border:1px solid var(--stroke);border-radius:12px;padding:24px;min-height:252px }}
    .phase .index {{ color:var(--muted);font-size:12px }}
    .phase h3 {{ margin:10px 0 15px;font-size:23px;letter-spacing:-.03em }}
    .stage {{ display:flex;justify-content:space-between;gap:12px;
      border-top:1px solid var(--stroke);padding:10px 0;font-size:14px }}
    .stage span:last-child {{ color:var(--mint);font-weight:650;white-space:nowrap }}
    .stage span.wait {{ color:var(--muted) }}
    .evidence {{ border:1px solid var(--stroke);background:var(--card);
      border-radius:14px;overflow:hidden;display:grid;grid-template-columns:1.3fr 1fr }}
    .evidence img {{ display:block;width:100%;height:auto;aspect-ratio:16/9;object-fit:contain;background:#080a0f }}
    .evidence aside {{ padding:27px;display:flex;flex-direction:column;justify-content:center }}
    .evidence h3 {{ margin:0 0 10px;font-size:25px;letter-spacing:-.035em }}
    .evidence p {{ color:var(--muted) }}
    .button {{ display:inline-block;padding:11px 16px;border:1px solid #7bbf9f;
      border-radius:9px;color:var(--mint);font-weight:650;text-decoration:none;
      margin:10px 10px 0 0 }}
    .button:hover {{ background:#283d35 }}
    .warning {{ border:1px solid #66563e;background:#29231b;padding:20px;border-radius:12px;
      color:#f0d8b4;margin-top:32px }}
    .digests {{ margin:20px 0; background:#121419;border-radius:12px;border:1px solid var(--stroke);padding:20px }}
    .digest {{ border-bottom:1px solid var(--stroke);padding:12px 0 }}
    .digest:last-child {{ border:0 }}
    .digest small {{ display:block;color:var(--muted);text-transform:uppercase;
      letter-spacing:.08em;font-size:11px }}
    .digest code {{ font:12px/1.6 ui-monospace,SFMono-Regular,Consolas,monospace;
      color:#c4cad6;overflow-wrap:anywhere }}
    footer {{ margin-top:38px;color:var(--muted);font-size:12px }}
    @media(max-width:780px) {{main{{padding:25px 17px}} h1{{margin-top:52px}}
      .metrics,.phases,.evidence{{grid-template-columns:1fr}} .metric{{padding:18px}}
      .phase{{min-height:initial}} .sub{{font-size:17px}} }}
  </style>
</head>
<body>
<main>
  <header class="brand">
    <span>Semwright &nbsp; × &nbsp; Motionwright</span>
    <span class="pill">Exact-SHA native execution proof</span>
  </header>
  <h1>Recovery.<br><span class="accent">Not repetition.</span></h1>
  <p class="sub">One persisted creative project, two actual application drivers,
    three independent Motionwright processes. Blender produces a real GLB once;
    Motion Canvas continues without restarting valid work.</p>
  <section class="metrics" aria-label="Measured execution">
    <div class="metric"><strong>0</strong><label>Blender native redispatches on resume</label></div>
    <div class="metric"><strong>3</strong><label>Separate Motionwright processes</label></div>
    <div class="metric"><strong>{frame_count}</strong><label>Motion Canvas native frames independently verified</label></div>
  </section>
  <p class="eyebrow">Receipt timeline</p>
  <h2>The job survives the process.</h2>
  <section class="phases">
    <article class="phase">
      <div class="index">01 &nbsp; / &nbsp; First process</div>
      <h3>Checkpoint</h3>
      <div class="stage"><span>Blender GLB</span><span>Executed ✓</span></div>
      <div class="stage"><span>Motion Canvas</span><span class="wait">Not started</span></div>
      <p>Native Blender finished. The application intentionally exited before Motion Canvas dispatch.</p>
    </article>
    <article class="phase">
      <div class="index">02 &nbsp; / &nbsp; Second process</div>
      <h3>Resume</h3>
      <div class="stage"><span>Blender GLB</span><span>Reused ✓</span></div>
      <div class="stage"><span>Motion Canvas</span><span>Executed ✓</span></div>
      <p>A fresh process reopens receipts, verifies Blender's exact GLB and renders native frames.</p>
    </article>
    <article class="phase">
      <div class="index">03 &nbsp; / &nbsp; Third process</div>
      <h3>Repeat</h3>
      <div class="stage"><span>Blender GLB</span><span>Reused ✓</span></div>
      <div class="stage"><span>Motion Canvas</span><span>Reused ✓</span></div>
      <p>Every recorded Motion Canvas PNG digest is rechecked; no expensive stages rerun.</p>
    </article>
  </section>
  <p class="eyebrow">Native media readback</p>
  <h2>Not a mocked completion state.</h2>
  <section class="evidence">
    <img src="middle-frame.png" alt="Actual native frame 30 from Motion Canvas after restart">
    <aside>
      <h3>Native frame 30 / 60</h3>
      <p>This preview is a byte-verified frame produced by the actual
        Semwright Motion Canvas Driver Host, paired with the Blender GLB
        produced in the previous application process.</p>
      <div>
        <a class="button" href="reused-blender.glb">Inspect original GLB ↗</a>
        <a class="button" href="artifact-manifest.json">Frame manifest ↗</a>
      </div>
    </aside>
  </section>
  <p class="eyebrow">Source-bound evidence</p>
  <h2>Follow the bytes.</h2>
  <section class="digests">
    <div class="digest"><small>Motionwright source SHA</small><code>{mw}</code></div>
    <div class="digest"><small>Pinned Semwright Native SDK / Broker SHA</small><code>{sw}</code></div>
    <div class="digest"><small>Reused Blender GLB SHA-256</small><code>{glb}</code></div>
    <div class="digest"><small>Motion Canvas artifact manifest SHA-256</small><code>{manifest}</code></div>
  </section>
  <div class="warning"><b>What this proves — and what it does not.</b><br>
    This run deliberately interrupts the process <em>between</em> Blender and Motion Canvas.
    It proves persisted native cross-app reuse after restart. It is
    <strong>not</strong> a real Motion Canvas renderer crash/failure test;
    the terminal-failure retry currently has separate controlled Rust tests.
    The GLB and Motion Canvas frames are two separate verified contributions,
    not a composited finished video.</div>
  <footer>Generated from complete real native E2E evidence. No hosted scripts, remote assets, or simulated stage status.</footer>
</main>
</body>
</html>
"""
    path = evidence / "recovery-demo.html"
    path.write_text(page, encoding="utf-8")
    return path
