from __future__ import annotations

import json
from pathlib import Path

# Single-file front-end: native JS + SVG/CSS, no CDN, no framework. Served at
# GET / and talks to /api/summary and /api/detail. Kept here so the package
# stays self-contained (no separate static-file dir to ship).
INDEX_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AIGameDevBench Reports</title>
<style>
  :root { --bg:#0f1117; --panel:#171a23; --line:#262a36; --fg:#e6e8ee;
          --muted:#8b91a1; --accent:#6ea8fe; }
  * { box-sizing: border-box; }
  body { margin:0; background:var(--bg); color:var(--fg);
         font:14px/1.5 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; }
  header { padding:14px 18px; border-bottom:1px solid var(--line);
           display:flex; align-items:center; gap:16px; flex-wrap:wrap; }
  h1 { font-size:16px; margin:0; font-weight:600; }
  h2 { font-size:13px; text-transform:uppercase; letter-spacing:.06em;
       color:var(--muted); margin:0 0 10px; }
  .wrap { padding:18px; display:grid; gap:18px; }
  .panel { background:var(--panel); border:1px solid var(--line);
           border-radius:8px; padding:16px; }
  button { background:#222634; color:var(--fg); border:1px solid var(--line);
           border-radius:6px; padding:5px 10px; cursor:pointer; font:inherit; }
  button:hover { border-color:var(--accent); }
  .runs { display:flex; gap:10px; flex-wrap:wrap; }
  .run-chip { display:flex; align-items:center; gap:7px; padding:6px 10px;
              border:1px solid var(--line); border-radius:6px; cursor:pointer;
              user-select:none; }
  .run-chip input { accent-color:var(--accent); }
  .run-chip small { color:var(--muted); }
  .bar-row { display:grid; grid-template-columns:160px 1fr 56px;
             align-items:center; gap:10px; margin:5px 0; }
  .bar-track { background:#0c0e14; border-radius:4px; height:18px;
               overflow:hidden; }
  .bar-fill { height:100%; }
  .num { text-align:right; color:var(--muted); }
  .num .ci { color:var(--muted); font-weight:normal; }
  table { border-collapse:collapse; width:100%; }
  th, td { border:1px solid var(--line); padding:5px 7px; text-align:center; }
  th.tc, td.tc { text-align:left; white-space:nowrap; }
  td.cell { cursor:pointer; font-variant-numeric:tabular-nums; }
  td.cell:hover { outline:2px solid var(--accent); outline-offset:-2px; }
  .miss { color:#555; background:#12141b; }
  .cats { display:grid; gap:6px; }
  .cat-line { display:grid; grid-template-columns:130px 1fr; gap:8px;
              align-items:center; }
  .grp { display:grid; grid-template-columns:90px 1fr 44px;
         align-items:center; gap:8px; margin:2px 0; }
  .badge { display:inline-block; padding:1px 7px; border-radius:10px;
           font-size:12px; margin-left:6px; }
  .badge.warn { background:#3a2a12; color:#f0b86e; }
  .badge.ok { background:#16301f; color:#74d99f; }
  #overlay { position:fixed; inset:0; background:rgba(0,0,0,.55);
             display:none; align-items:flex-start; justify-content:center;
             padding:40px 16px; overflow:auto; }
  #overlay.show { display:flex; }
  .modal { background:var(--panel); border:1px solid var(--line);
           border-radius:10px; max-width:1100px; width:100%; padding:18px; }
  .modal-head { display:flex; justify-content:space-between; align-items:center;
                margin-bottom:12px; }
  .detail-cols { display:grid; gap:14px;
                 grid-template-columns:repeat(auto-fit,minmax(320px,1fr)); }
  .check { border:1px solid var(--line); border-radius:6px; padding:8px 10px;
           margin:6px 0; }
  .check.pass { border-left:3px solid #74d99f; }
  .check.fail { border-left:3px solid #f06e6e; }
  .check .nm { font-weight:600; }
  .check .ea { color:var(--muted); margin-top:3px; word-break:break-word; }
  .check .ea b { color:var(--fg); font-weight:600; }
  .empty { color:var(--muted); padding:20px; text-align:center; }
  .err { color:#f0a0a0; white-space:pre-wrap; }
  a { color:var(--accent); }
  .tabs { display:flex; gap:6px; }
  .tab { background:transparent; border:1px solid var(--line); }
  .tab.active { background:#222634; border-color:var(--accent); color:var(--fg); }
  .tc-grid { display:grid; gap:8px;
             grid-template-columns:repeat(auto-fill,minmax(240px,1fr)); }
  .tc-item { text-align:left; border:1px solid var(--line); border-radius:6px;
             padding:9px 11px; cursor:pointer; }
  .tc-item:hover { border-color:var(--accent); background:#1b1f2a; }
  .tc-item small { color:var(--muted); display:block; margin-top:2px; }
  /* editable / friendlier testcases view */
  button.primary { background:#1c3a5e; border-color:var(--accent); color:#dbe8ff; }
  button.danger { background:#3a1818; border-color:#7a3a3a; color:#f0b0b0; }
  button.sm { padding:2px 8px; font-size:12px; }
  .tc-toolbar { display:flex; align-items:center; gap:10px; flex-wrap:wrap;
                margin-bottom:12px; }
  .tc-search { background:#0c0e14; border:1px solid var(--line); border-radius:6px;
               color:var(--fg); padding:6px 10px; font:inherit; min-width:200px; }
  .tc-filters { display:flex; gap:5px; flex-wrap:wrap; }
  .tc-count { color:var(--muted); font-size:12px; }
  .chip { border:1px solid var(--line); border-radius:12px; padding:2px 10px;
          font-size:12px; cursor:pointer; background:transparent; color:var(--muted); }
  .chip.on { border-color:var(--accent); color:var(--fg); background:#1b2432; }
  .tc-card { position:relative; text-align:left; border:1px solid var(--line);
             border-radius:8px; padding:11px 12px; cursor:pointer;
             display:flex; flex-direction:column; gap:7px; }
  .tc-card:hover { border-color:var(--accent); background:#1b1f2a; }
  .tc-card .tc-id { font-weight:600; word-break:break-all; }
  .tc-card .tc-task { color:var(--muted); font-size:12px; line-height:1.4;
                      display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical;
                      overflow:hidden; }
  .tags { display:flex; gap:5px; flex-wrap:wrap; }
  .tag { font-size:11px; padding:1px 8px; border-radius:10px; white-space:nowrap; }
  .tag.cat { color:#0c0e14; font-weight:600; }
  .tag.vf { background:#222634; color:#aeb6c6; border:1px solid var(--line); }
  .tag.files { background:transparent; color:var(--muted); border:1px solid var(--line); }
  /* category color palette */
  .cat-behavior_logic { background:#6ea8fe; }
  .cat-intent_translation { background:#74d99f; }
  .cat-precise_edit { background:#f0c26e; }
  .cat-architecture { background:#c79bf0; }
  .cat-visual_audio { background:#f0907e; }
  .cat-unknown { background:#8b91a1; }
  .edit-form { display:grid; gap:10px; margin-bottom:8px; }
  .edit-form label { display:grid; gap:4px; font-size:12px; color:var(--muted); }
  .edit-form input, .edit-form select, .edit-form textarea {
      background:#0c0e14; border:1px solid var(--line); border-radius:6px;
      color:var(--fg); padding:6px 9px; font:inherit; }
  .edit-form textarea { min-height:70px; resize:vertical; white-space:pre-wrap; }
  .row-btns { display:flex; gap:8px; align-items:center; flex-wrap:wrap; }
  .file-actions { display:flex; gap:6px; align-items:center; }
  .file-edit-area { width:100%; min-height:180px; background:#0c0e14; color:#cdd3df;
                    border:0; border-top:1px solid var(--line); padding:10px;
                    font:12px/1.45 inherit; white-space:pre; resize:vertical; }
  .save-note { font-size:12px; color:var(--muted); }
  .save-note.ok { color:#74d99f; }
  .save-note.err { color:#f0a0a0; }
  .kv { display:grid; grid-template-columns:120px 1fr; gap:4px 10px;
        margin-bottom:12px; }
  .kv .k { color:var(--muted); }
  .task-box { background:#0c0e14; border:1px solid var(--line); border-radius:6px;
              padding:10px; white-space:pre-wrap; margin-bottom:12px; }
  .files { list-style:none; padding:0; margin:0; columns:2; }
  .files li { padding:1px 0; color:var(--fg); break-inside:avoid; }
  .files li small { color:var(--muted); }
  .pill { display:inline-block; padding:1px 8px; border:1px solid var(--line);
          border-radius:10px; font-size:12px; }
  .crumb { display:flex; align-items:center; gap:8px; margin-bottom:12px;
           color:var(--muted); }
  .crumb a { cursor:pointer; }
  .content-layout { display:grid; grid-template-columns:minmax(220px,280px) 1fr;
                    gap:14px; align-items:start; }
  .content-list { display:flex; flex-direction:column; gap:4px; max-height:74vh;
                  overflow:auto; }
  .content-item { text-align:left; border:1px solid var(--line); border-radius:6px;
                  padding:7px 10px; cursor:pointer; color:var(--fg);
                  text-decoration:none; }
  .content-item:hover { border-color:var(--accent); background:#1b1f2a; }
  .content-item.active { background:#222634; border-color:var(--accent); }
  .content-item small { color:var(--muted); display:block; margin-top:2px; }
  .file-block { border:1px solid var(--line); border-radius:6px; margin:8px 0;
                overflow:hidden; }
  .file-block > summary { cursor:pointer; padding:7px 10px; background:#12141b;
                          display:flex; justify-content:space-between; gap:10px; }
  .file-block small { color:var(--muted); }
  .file-pre { margin:0; padding:10px; max-height:430px; overflow:auto;
              white-space:pre-wrap; word-break:break-word; font:12px/1.45 inherit;
              background:#0c0e14; color:#cdd3df; }
  .file-note { padding:10px; color:var(--muted); background:#0c0e14; }
  @media (max-width: 800px) {
    .content-layout { grid-template-columns:1fr; }
    .content-list { max-height:none; }
  }
  .act { margin-top:12px; }
  .act h3 { font-size:12px; text-transform:uppercase; letter-spacing:.05em;
            color:var(--muted); margin:10px 0 6px; }
  details.turn { border:1px solid var(--line); border-radius:6px; margin:6px 0;
                 padding:6px 10px; }
  details.turn > summary { cursor:pointer; color:var(--fg); }
  details.turn[open] > summary { margin-bottom:6px; }
  details.turn.turn-bottleneck { border-color:#e5484d; box-shadow:0 0 0 1px #e5484d33; }
  .bottleneck { color:#e5484d; font-weight:600; font-size:11px; }
  .turn .lbl { color:var(--muted); margin:6px 0 2px; }
  .logbox { background:#0c0e14; border:1px solid var(--line); border-radius:6px;
            padding:10px; max-height:320px; overflow:auto; white-space:pre-wrap;
            word-break:break-word; font-size:12px; color:#cdd3df; }
  .tools { list-style:none; padding:0; margin:4px 0 0; }
  .tools li { font-size:12px; color:#9fb4d8; padding:1px 0;
              white-space:pre-wrap; word-break:break-word; }
  /* Run tab */
  .run-form { display:grid; gap:12px;
              grid-template-columns:repeat(auto-fit,minmax(240px,1fr)); }
  .run-form label { display:flex; flex-direction:column; gap:4px;
                    font-size:12px; color:var(--muted); }
  .run-form .run-cmd-row, .run-form .run-patch-row,
  .run-form .run-harness-row { grid-column:1/-1; }
  .run-form input, .run-form select { background:#0c0e14; border:1px solid var(--line);
              border-radius:6px; color:var(--fg); padding:6px 9px; font:inherit; }
  .run-actions { display:flex; align-items:center; gap:12px; margin-top:14px; }
  .run-msg { color:var(--muted); font-size:12px; }
  .run-msg.err { color:#f0a0a0; }
  .run-intro { color:var(--muted); font-size:12px; margin-bottom:12px;
               line-height:1.5; }
  .run-infra { margin-top:12px; display:grid; gap:3px 12px; font-size:12px;
               grid-template-columns:130px 1fr; }
  .run-infra .k { color:var(--muted); }
  .run-infra .v { word-break:break-all; }
  .run-status-head { display:flex; align-items:center; gap:12px; flex-wrap:wrap; }
  .run-cmdline { color:var(--muted); font-size:12px; margin-top:8px;
                 word-break:break-all; }
  .run-log { background:#0c0e14; border:1px solid var(--line); border-radius:6px;
             padding:10px 12px; max-height:420px; overflow:auto; white-space:pre-wrap;
             word-break:break-word; font-size:12px; color:#cdd6e6; }
  .badge.run { background:#12314f; color:#8fc0ff; }
  .badge.done { background:#16301f; color:#74d99f; }
  .badge.failed { background:#3a1818; color:#f0b0b0; }
  .badge.idle { background:#222634; color:#aeb6c6; }
  /* header live indicator (shown on every tab) */
  .live-indicator { display:none; align-items:center; gap:6px; font-size:12px;
                    padding:3px 9px; border-radius:12px; border:1px solid var(--line); }
  .live-indicator.show { display:inline-flex; }
  .live-indicator .dot { width:8px; height:8px; border-radius:50%; background:var(--muted); }
  .live-indicator.running { border-color:#3a5da8; color:#8fc0ff; }
  .live-indicator.running .dot { background:#6ea8fe; animation:pulse 1.2s ease-in-out infinite; }
  .live-indicator.done { border-color:#2c6b45; color:#74d99f; }
  .live-indicator.done .dot { background:#74d99f; }
  .live-indicator.failed { border-color:#7a3a3a; color:#f0b0b0; }
  .live-indicator.failed .dot { background:#f06e6e; }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.35} }
  /* Status tab */
  .st-head { display:flex; align-items:center; gap:12px; flex-wrap:wrap; margin-bottom:14px; }
  .st-title { font-weight:600; }
  .st-bar-wrap { display:flex; align-items:center; gap:12px; }
  .st-bar-track { flex:1; background:#0c0e14; border:1px solid var(--line);
                  border-radius:6px; height:22px; overflow:hidden; }
  .st-bar-fill { height:100%; width:0%; background:linear-gradient(90deg,#3a5da8,#6ea8fe);
                 transition:width .4s ease; }
  .st-bar-num { color:var(--muted); font-variant-numeric:tabular-nums; min-width:70px;
                text-align:right; }
  .st-grid { display:grid; gap:6px;
             grid-template-columns:repeat(auto-fill,minmax(230px,1fr)); }
  .st-cell { display:flex; align-items:center; gap:8px; border:1px solid var(--line);
             border-radius:6px; padding:6px 10px; font-size:12px; background:#0c0e14; }
  .st-cell .nm { flex:1; word-break:break-all; }
  .st-cell .sc { font-variant-numeric:tabular-nums; color:var(--muted); }
  .st-cell.pending { opacity:.55; }
  .st-cell.pass { border-left:3px solid #74d99f; }
  .st-cell.fail { border-left:3px solid #f06e6e; }
  .st-cell.run  { border-left:3px solid #6ea8fe; }
  /* Webhooks tab */
  .wh-toolbar { display:flex; align-items:center; gap:12px; flex-wrap:wrap;
                margin-bottom:12px; }
  .wh-auto { display:flex; align-items:center; gap:6px; color:var(--muted);
             font-size:12px; }
  .hook { background:#0c0e14; border:1px solid var(--line); border-radius:8px;
          overflow:hidden; margin:6px 0; }
  .hook > summary { cursor:pointer; list-style:none; padding:9px 12px;
          display:flex; align-items:center; gap:10px; flex-wrap:wrap; }
  .hook > summary::-webkit-details-marker { display:none; }
  .hook .id { font-weight:600; word-break:break-all; }
  .hook .from { color:var(--muted); font-size:12px; }
  .hook .when { color:var(--muted); font-size:12px; }
  .hook .body { border-top:1px solid var(--line); padding:11px 13px; }
  .hook .body pre { margin:0; white-space:pre-wrap; word-break:break-word;
                    font-size:12px; color:#cdd6e6; }
  .hook .kv { display:grid; grid-template-columns:110px 1fr; gap:3px 12px;
              margin-bottom:9px; font-size:12px; }
  .hook .kv .k { color:var(--muted); }
  .badge.b-accepted { background:#16301f; color:#74d99f; }
  .badge.b-skipped  { background:#3a2a12; color:#f0b86e; }
  .badge.b-error    { background:#3a1818; color:#f0b0b0; }
  .badge.b-event    { background:#222634; color:#aeb6c6; border:1px solid var(--line); }
</style>
</head>
<body>
<header>
  <h1>AIGameDevBench</h1>
  <nav class="tabs">
    <button id="tab-reports" class="tab active">Reports</button>
    <button id="tab-testcases" class="tab">Testcases</button>
    <button id="tab-contents" class="tab">Contents</button>
    <button id="tab-run" class="tab">Run</button>
    <button id="tab-status" class="tab">Status</button>
    <button id="tab-webhooks" class="tab">Webhooks</button>
  </nav>
  <span id="live-indicator" class="live-indicator" title="Benchmark run status"></span>
  <button id="refresh">Refresh</button>
  <span id="status" style="color:var(--muted)"></span>
</header>
<div id="view-reports" class="wrap">
  <div class="panel">
    <h2>Runs</h2>
    <div id="runs" class="runs"></div>
  </div>
  <div class="panel">
    <h2>Overall &amp; per-category</h2>
    <div id="overall"></div>
  </div>
  <div class="panel">
    <h2>Score matrix (click a cell to drill in)</h2>
    <div id="matrix" style="overflow:auto"></div>
  </div>
  <div class="panel">
    <h2>Timing &amp; stability</h2>
    <div id="timing"></div>
  </div>
</div>
<div id="view-testcases" class="wrap" style="display:none">
  <div class="panel">
    <div class="tc-toolbar">
      <input id="tc-search" class="tc-search" type="search" placeholder="Search id / task...">
      <span id="tc-filters" class="tc-filters"></span>
      <span style="flex:1"></span>
      <span id="tc-count" class="tc-count"></span>
      <button id="tc-new" class="primary" style="display:none">+ New testcase</button>
    </div>
    <div id="tc-grid" class="tc-grid"></div>
  </div>
</div>
<div id="view-testcase" class="wrap" style="display:none">
  <div class="panel">
    <div class="crumb">
      <a id="tc-back">&larr; Testcases</a>
    </div>
    <div id="tc-detail"><span class="empty">Loading...</span></div>
  </div>
</div>
<div id="view-contents" class="wrap" style="display:none">
  <div class="panel">
    <h2>Testcase contents</h2>
    <div class="content-layout">
      <div id="content-list" class="content-list"></div>
      <div id="content-detail"><span class="empty">Select a testcase.</span></div>
    </div>
  </div>
</div>

<div id="view-run" class="wrap" style="display:none">
  <div class="panel">
    <h2>Launch a benchmark run (docker + Kubernetes)</h2>
    <div id="run-disabled" class="empty" style="display:none">
      Running is disabled. Restart the dashboard with <code>--allow-run</code>.
    </div>
    <div id="run-intro" class="run-intro">
      Fans out one Kubernetes Job per testcase on the shared runner image (the
      same production path the PR-candidate flow uses). The harness comes from
      the cluster Secret; results are aggregated into a normal report and shown
      in the Reports tab under your run name.
    </div>
    <div id="run-form" class="run-form">
      <label>run name (required)
        <input id="run-name" type="text" placeholder="e.g. claude-2026-07-13">
      </label>
      <label>runner image (tag from beaver_hub-public)
        <select id="run-image"><option value="">loading…</option></select>
      </label>
      <label>testcases (blank = all; space/comma-separated ids)
        <input id="run-testcases" type="text" placeholder="leave blank for all">
      </label>
      <label class="run-harness-row">harness command (blank = Secret default; {task} is substituted)
        <input id="run-harness" type="text"
               placeholder="e.g. claude -p {task} --dangerously-skip-permissions">
      </label>
      <label>jobs (max concurrent k8s Jobs)
        <input id="run-jobs" type="number" value="16" min="1">
      </label>
      <label>per-testcase timeout (s)
        <input id="run-timeout" type="number" value="1200" min="1">
      </label>
    </div>
    <div class="run-infra" id="run-infra"></div>
    <div class="run-actions">
      <button id="run-start" class="primary">Start benchmark</button>
      <button id="run-stop" class="danger" disabled>Stop</button>
      <span id="run-msg" class="run-msg"></span>
    </div>
  </div>
  <div class="panel">
    <h2>Live status</h2>
    <div id="run-status-head" class="run-status-head">
      <span id="run-badge" class="badge">idle</span>
      <span id="run-elapsed" class="tc-count"></span>
      <span id="run-report" class="tc-count"></span>
    </div>
    <div id="run-meta" class="run-cmdline"></div>
    <div id="run-cmdline" class="run-cmdline"></div>
    <h2 style="margin-top:14px">Matrix log (docker build/push skipped · k8s Jobs)</h2>
    <pre id="run-log" class="run-log">No run yet.</pre>
  </div>
</div>

<div id="view-status" class="wrap" style="display:none">
  <div class="panel">
    <div class="st-head">
      <span id="st-badge" class="badge idle">idle</span>
      <span id="st-title" class="st-title">No benchmark running</span>
      <span style="flex:1"></span>
      <span id="st-elapsed" class="tc-count"></span>
    </div>
    <div class="st-bar-wrap">
      <div class="st-bar-track"><div id="st-bar-fill" class="st-bar-fill"></div></div>
      <span id="st-bar-num" class="st-bar-num">0 / 0</span>
    </div>
    <div id="st-meta" class="run-cmdline"></div>
  </div>
  <div class="panel">
    <h2>Per-testcase progress</h2>
    <div id="st-grid" class="st-grid"><span class="empty">No run yet.</span></div>
  </div>
</div>

<div id="view-webhooks" class="wrap" style="display:none">
  <div class="panel">
    <div class="wh-toolbar">
      <h2 style="margin:0">Received webhooks</h2>
      <span id="wh-count" class="tc-count"></span>
      <span style="flex:1"></span>
      <button id="wh-loadall" class="sm">Load all</button>
      <label class="wh-auto"><input id="wh-auto" type="checkbox" checked> auto-refresh (3s)</label>
      <button id="wh-refresh" class="sm">Refresh</button>
    </div>
    <div id="wh-disabled" class="empty" style="display:none">
      No webhook log configured. Restart the dashboard with
      <code>--webhook-log &lt;path&gt;</code> (the webhook receiver writes
      <code>.orchestrator/webhooks.jsonl</code>).
    </div>
    <div id="wh-list"><span class="empty">Loading...</span></div>
  </div>
</div>

<div id="overlay"><div class="modal">
  <div class="modal-head">
    <h2 id="modal-title" style="margin:0"></h2>
    <button id="close">Close</button>
  </div>
  <div id="modal-body"></div>
</div></div>

<script>
const $ = (s, el=document) => el.querySelector(s);
let SUMMARY = null;
let SELECTED = new Set();

function scoreColor(s) {
  if (s === null || s === undefined) return null;
  // red(0) -> yellow(0.5) -> green(1): hue 0..120
  const hue = Math.max(0, Math.min(1, s)) * 120;
  return `hsl(${hue},55%,32%)`;
}
function fmt(n, d=2) {
  return (n === null || n === undefined) ? "-" : Number(n).toFixed(d);
}
function esc(v) {
  if (v === null || v === undefined) return "null";
  return String(v).replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
}
function selectedRuns() {
  return SUMMARY.runs.filter(r => SELECTED.has(r.run_id));
}

async function load() {
  $("#status").textContent = "loading...";
  const res = await fetch("/api/summary");
  SUMMARY = await res.json();
  if (SELECTED.size === 0)
    SUMMARY.runs.forEach(r => SELECTED.add(r.run_id));
  else
    SELECTED = new Set([...SELECTED].filter(
      id => SUMMARY.runs.some(r => r.run_id === id)));
  $("#status").textContent =
    `${SUMMARY.runs.length} run(s), ${SUMMARY.testcases.length} testcase(s)`;
  renderRuns(); renderAll();
}

function renderRuns() {
  const box = $("#runs");
  if (!SUMMARY.runs.length) { box.innerHTML = '<span class="empty">No reports found.</span>'; return; }
  box.innerHTML = "";
  for (const r of SUMMARY.runs) {
    const d = new Date(r.mtime * 1000);
    const lab = document.createElement("label");
    lab.className = "run-chip";
    lab.innerHTML = `<input type="checkbox" ${SELECTED.has(r.run_id)?"checked":""}>
      <span>${esc(r.harness)} <small>${esc(r.file)}</small></span>
      <small>${fmt(r.mean_score)} · ${d.toLocaleString()}</small>`;
    lab.querySelector("input").addEventListener("change", e => {
      e.target.checked ? SELECTED.add(r.run_id) : SELECTED.delete(r.run_id);
      renderAll();
    });
    box.appendChild(lab);
  }
}

function renderAll() { renderOverall(); renderMatrix(); renderTiming(); }

function renderOverall() {
  const runs = selectedRuns();
  const el = $("#overall");
  if (!runs.length) { el.innerHTML = '<span class="empty">Select a run.</span>'; return; }
  let h = "<h2>Mean score</h2>";
  for (const r of runs) {
    // With --repeat, show the suite mean ± half-CI and an N badge so a
    // single-sample run (no variance) is visually distinct from a repeated one.
    let extra = "";
    const ci = r.mean_score_ci95;
    if (r.repeat && r.repeat > 1 && Array.isArray(ci)) {
      const half = (ci[1] - ci[0]) / 2;
      extra = ` <small class="ci">±${fmt(half)} · repeat ${r.repeat}</small>`;
    }
    h += barRow(r.harness, r.mean_score, extra, ci);
  }
  // per-category grouped
  const cats = [...new Set(runs.flatMap(r =>
    Object.keys(SUMMARY.categories[r.run_id] || {})))].sort();
  if (cats.length) {
    h += '<h2 style="margin-top:14px">Per-category mean</h2>';
    for (const c of cats) {
      h += `<div class="cat-line"><div>${esc(c)}</div><div>`;
      for (const r of runs) {
        const cd = (SUMMARY.categories[r.run_id]||{})[c];
        const m = cd ? cd.mean : null;
        h += `<div class="grp"><small>${esc(r.harness)}</small>
          <div class="bar-track"><div class="bar-fill"
            style="width:${(m||0)*100}%;background:${scoreColor(m)||'#333'}"></div></div>
          <span class="num">${fmt(m)}</span></div>`;
      }
      h += "</div></div>";
    }
  }
  el.innerHTML = h;
}
function barRow(label, score, extra, ci) {
  // Optional CI whiskers: a translucent band spanning [ci_lo, ci_hi] behind the
  // fill, so the uncertainty of a repeated run is visible at a glance.
  let band = "";
  if (Array.isArray(ci) && (ci[1] - ci[0]) > 1e-9) {
    const left = (ci[0] || 0) * 100, width = (ci[1] - ci[0]) * 100;
    band = `<div style="position:absolute;top:0;bottom:0;left:${left}%;width:${width}%;
      background:rgba(255,255,255,0.18)"></div>`;
  }
  return `<div class="bar-row"><div>${esc(label)}</div>
    <div class="bar-track" style="position:relative">${band}<div class="bar-fill"
      style="width:${(score||0)*100}%;background:${scoreColor(score)||'#333'}"></div></div>
    <span class="num">${fmt(score)}${extra||""}</span></div>`;
}

function renderMatrix() {
  const runs = selectedRuns();
  const el = $("#matrix");
  if (!runs.length || !SUMMARY.testcases.length) {
    el.innerHTML = '<span class="empty">Nothing to show.</span>'; return; }
  let h = "<table><thead><tr><th class='tc'>testcase</th>";
  for (const r of runs) h += `<th>${esc(r.harness)}<br><small>${esc(r.file)}</small></th>`;
  h += "</tr></thead><tbody>";
  for (const tc of SUMMARY.testcases) {
    h += `<tr><td class='tc'>${esc(tc)}</td>`;
    for (const r of runs) {
      const s = SUMMARY.matrix[tc][r.run_id];
      if (s === null || s === undefined) {
        h += `<td class="cell miss">-</td>`;
      } else {
        h += `<td class="cell" style="background:${scoreColor(s)}"
          data-run="${esc(r.run_id)}" data-tc="${esc(tc)}">${fmt(s)}</td>`;
      }
    }
    h += "</tr>";
  }
  h += "</tbody></table>";
  el.innerHTML = h;
  el.querySelectorAll("td.cell[data-tc]").forEach(td =>
    td.addEventListener("click", () => openDetail(td.dataset.tc, td.dataset.run)));
}

function renderTiming() {
  const runs = selectedRuns();
  const el = $("#timing");
  if (!runs.length) { el.innerHTML = '<span class="empty">Select a run.</span>'; return; }
  const maxTotal = Math.max(...runs.map(r => (SUMMARY.timing[r.run_id]||{}).total_wall_time || 0), 1);
  let h = "";
  for (const r of runs) {
    const t = SUMMARY.timing[r.run_id] || {};
    let badges = "";
    if (t.timed_out) badges += `<span class="badge warn">timeout ${t.timed_out}</span>`;
    if (t.stalled) badges += `<span class="badge warn">stalled ${t.stalled}</span>`;
    if (t.blocked_on_approval) badges += `<span class="badge warn">blocked ${t.blocked_on_approval}</span>`;
    if (!badges) badges = `<span class="badge ok">clean</span>`;
    // Failure funnel: count of runs stopped at each pipeline layer. `none` means
    // the verifier ran (score may still be < 1 — a capability gap, not a stall).
    const fs = (SUMMARY.failure_stages || {})[r.run_id] || {};
    let funnel = "";
    for (const s of ["no_change","harness_error","l0","l1","verifier"]) {
      if (fs[s]) funnel += `<span class="badge warn">${s} ${fs[s]}</span>`;
    }
    if (fs.none) funnel += `<span class="badge ok">ran ${fs.none}</span>`;
    // Per-stage mean cost (ms): where the pipeline time actually goes.
    const sm = t.stage_means || {};
    const stageBits = [];
    for (const [k,lbl] of [["import_ms","import"],["l0_ms","L0"],["l1_ms","L1"],["verifier_ms","verify"]]) {
      if (sm[k] != null) stageBits.push(`${lbl} ${fmt(sm[k]/1000,2)}s`);
    }
    const stageLine = stageBits.length
      ? `<div style="margin:0 0 8px 170px;color:var(--muted);font-size:12px">per-stage mean: ${stageBits.join(" · ")}</div>` : "";
    h += `<div class="bar-row"><div>${esc(r.harness)}</div>
      <div class="bar-track"><div class="bar-fill"
        style="width:${((t.total_wall_time||0)/maxTotal)*100}%;background:#3b6ea5"></div></div>
      <span class="num">${fmt(t.total_wall_time,1)}s</span></div>
      <div style="margin:-2px 0 4px 170px;color:var(--muted)">
        mean ${fmt(t.mean_wall_time,1)}s ${badges} ${funnel}</div>${stageLine}`;
  }
  el.innerHTML = h;
}

async function openDetail(tc, runId) {
  // If a specific cell was clicked (runId given), scope the drill-down to that
  // single run; otherwise compare across all selected runs.
  let runs = selectedRuns();
  if (runId) {
    const one = SUMMARY.runs.find(r => r.run_id === runId);
    if (one) runs = [one];
  }
  $("#modal-title").textContent = runId
    ? `${tc} — ${runs[0] ? runs[0].harness : ""}` : tc;
  $("#modal-body").innerHTML = "loading...";
  $("#overlay").classList.add("show");
  const details = await Promise.all(runs.map(r =>
    fetch(`/api/detail?run=${encodeURIComponent(r.run_id)}&testcase=${encodeURIComponent(tc)}`)
      .then(x => x.json()).then(d => ({run:r, detail:d})).catch(() => ({run:r, detail:null}))));
  let h = '<div class="detail-cols">';
  for (const {run, detail} of details) {
    h += `<div><h2>${esc(run.harness)} <small>${esc(run.file)}</small></h2>`;
    if (!detail) { h += '<div class="empty">not in this run</div></div>'; continue; }
    h += `<div style="margin-bottom:6px">score <b>${fmt(detail.score)}</b>
          · ${esc(detail.status)}`;
    if (detail.wall_time != null) h += ` · ${fmt(detail.wall_time,1)}s`;
    if (detail.failure_stage && detail.failure_stage !== "none")
      h += ` · <span class="badge warn">${esc(detail.failure_stage)}</span>`;
    h += "</div>";
    // Per-stage cost for this single run: exposes godot-import / L0 / verifier
    // time that wall_time (harness only) hides.
    const tm = detail.timings || {};
    const bits = [];
    for (const [k,lbl] of [["import_ms","import"],["l0_ms","L0"],["l1_ms","L1"],["verifier_ms","verify"],["total_ms","total"]]) {
      if (tm[k] != null) {
        // Label a skipped import so a near-zero number reads as "elided", not "free".
        const suffix = (k === "import_ms" && tm.import_skipped) ? " (skipped)" : "";
        bits.push(`${lbl} ${fmt(tm[k]/1000,2)}s${suffix}`);
      }
    }
    if (bits.length) h += `<div style="margin-bottom:6px;color:var(--muted);font-size:12px">${bits.join(" · ")}</div>`;
    // Repeat distribution: mini score bars for each attempt + mean/std/CI/pass@1,
    // so a stochastic harness's variance on THIS testcase is legible.
    const rp = detail.repeat;
    if (rp && Array.isArray(rp.scores) && rp.scores.length > 1) {
      const cells = rp.scores.map(s =>
        `<span title="${fmt(s)}" style="display:inline-block;width:14px;height:14px;margin:1px;
          border-radius:2px;background:${scoreColor(s)||'#333'}"></span>`).join("");
      const ci = rp.ci95 || [rp.mean, rp.mean];
      h += `<div class="act" style="margin:6px 0"><h3>Repeat (N=${rp.n})</h3>
        <div>${cells}</div>
        <div style="color:var(--muted);font-size:12px;margin-top:4px">
          mean ${fmt(rp.mean)} · std ${fmt(rp.std)} ·
          95% CI [${fmt(ci[0])}, ${fmt(ci[1])}] · pass@1 ${fmt(rp.pass_at_1)}</div></div>`;
    }
    if (detail.error) h += `<div class="err">${esc(detail.error)}</div>`;
    if (!detail.checks.length) h += '<div class="empty">no checks</div>';
    for (const c of detail.checks) {
      h += `<div class="check ${c.passed?'pass':'fail'}">
        <div class="nm">${c.passed?'✓':'✗'} ${esc(c.name)}</div>`;
      if (c.detail) h += `<div class="ea">${esc(c.detail)}</div>`;
      if (c.expected !== null || c.actual !== null)
        h += `<div class="ea">expected <b>${esc(c.expected)}</b> ·
              actual <b>${esc(c.actual)}</b></div>`;
      h += "</div>";
    }
    h += renderActivity(detail);
    h += "</div>";
  }
  h += "</div>";
  $("#modal-body").innerHTML = h;
}

// AI activity record for a run+testcase: per-turn input/output/tool_calls
// (survey runs), the harness log tail (command runs), and the diff.
function renderActivity(detail) {
  let h = "";
  const turns = detail.ai_turns || [];
  if (turns.length) {
    const slow = detail.slowest_turn || null;
    const slowTurn = slow ? slow.turn : null;
    h += '<div class="act"><h3>AI activity';
    if (detail.total_tokens) h += ` <small>(${detail.total_tokens} tokens)</small>`;
    if (detail.total_turn_ms != null) h += ` <small>· ${(detail.total_turn_ms/1000).toFixed(1)}s in turns</small>`;
    h += "</h3>";
    for (const t of turns) {
      // Per-turn duration in the summary; the single slowest turn is flagged as
      // the bottleneck so you can see WHERE the harness spent its time.
      const isBottleneck = (slowTurn != null && t.turn === slowTurn);
      const dur = (t.duration_ms != null) ? ` · ${(t.duration_ms/1000).toFixed(1)}s` : "";
      const flag = isBottleneck ? ' <span class="bottleneck">⚠ bottleneck</span>' : "";
      h += `<details class="turn${isBottleneck ? ' turn-bottleneck' : ''}" open>` +
           `<summary>turn ${esc(t.turn)}${dur}${flag}</summary>`;
      if (t.agent_input) h += `<div class="lbl">input</div><div class="logbox">${esc(t.agent_input)}</div>`;
      if (t.agent_output) h += `<div class="lbl">output</div><div class="logbox">${esc(t.agent_output)}</div>`;
      if (t.tool_calls && t.tool_calls.length) {
        h += `<div class="lbl">tool calls (${t.tool_calls.length})</div><ul class="tools">`;
        for (const tc of t.tool_calls) h += `<li>${esc(tc)}</li>`;
        h += "</ul>";
      }
      h += "</details>";
    }
    h += "</div>";
  }
  if (detail.log_text) {
    h += `<div class="act"><h3>Harness log${detail.log_path ? ` <small>${esc(detail.log_path)}</small>` : ""}</h3>
      <div class="logbox">${esc(detail.log_text)}</div></div>`;
  }
  if (detail.diff && detail.diff.trim()) {
    h += `<div class="act"><h3>Diff</h3><div class="logbox">${esc(detail.diff)}</div></div>`;
  }
  return h;
}

// ---- Testcases: card grid + filter/search + per-testcase edit sub-page ----
let TESTCASES = null;
let TESTCASE_DETAILS = new Map();
let CONFIG = null;              // {editable, has_testcases, enums}
let TC_SEARCH = "";
let TC_CAT_FILTER = new Set();  // active category filters (empty = all)

async function loadConfig() {
  if (CONFIG) return CONFIG;
  try { CONFIG = await (await fetch("/api/config")).json(); }
  catch (e) { CONFIG = {editable:false, has_testcases:true, enums:{}}; }
  return CONFIG;
}

async function loadTestcases(force=false) {
  if (TESTCASES && !force) return TESTCASES;
  try {
    TESTCASES = await (await fetch("/api/testcases")).json();
  } catch (e) { TESTCASES = []; }
  return TESTCASES;
}

function catClass(cat) {
  const known = ["behavior_logic","intent_translation","precise_edit","architecture","visual_audio"];
  return "cat-" + (known.includes(cat) ? cat : "unknown");
}

function firstLine(task) {
  const t = (task || "").trim().replace(/\s+/g, " ");
  return t.length > 140 ? t.slice(0, 140) + "..." : t;
}

async function renderTestcaseList() {
  const grid = $("#tc-grid");
  grid.innerHTML = '<span class="empty">loading...</span>';
  await loadConfig();
  await loadTestcases();
  // New button visibility depends on editable mode
  const newBtn = $("#tc-new");
  if (newBtn) newBtn.style.display = CONFIG.editable ? "" : "none";
  if (!TESTCASES.length) {
    grid.innerHTML = '<span class="empty">No testcases.<br>Run serve with --testcases-dir'
      + (CONFIG.editable ? ', then click "+ New testcase".' : '.') + '</span>';
    $("#tc-filters").innerHTML = "";
    $("#tc-count").textContent = "";
    return;
  }
  // Build category filter chips (once per render, reflecting current state)
  const cats = [...new Set(TESTCASES.map(t => t.category))].sort();
  const fb = $("#tc-filters");
  fb.innerHTML = "";
  for (const c of cats) {
    const chip = document.createElement("button");
    chip.className = "chip" + (TC_CAT_FILTER.has(c) ? " on" : "");
    chip.textContent = c;
    chip.onclick = () => {
      if (TC_CAT_FILTER.has(c)) TC_CAT_FILTER.delete(c); else TC_CAT_FILTER.add(c);
      renderTestcaseList();
    };
    fb.appendChild(chip);
  }
  // Apply search + filter
  const q = TC_SEARCH.toLowerCase();
  const shown = TESTCASES.filter(tc => {
    if (TC_CAT_FILTER.size && !TC_CAT_FILTER.has(tc.category)) return false;
    if (q && !(tc.id.toLowerCase().includes(q) || (tc.task || "").toLowerCase().includes(q)))
      return false;
    return true;
  });
  $("#tc-count").textContent = `${shown.length} / ${TESTCASES.length} shown`;
  grid.innerHTML = "";
  if (!shown.length) {
    grid.innerHTML = '<span class="empty">No testcases match the filter.</span>';
    return;
  }
  for (const tc of shown) {
    const a = document.createElement("a");
    a.className = "tc-card";
    a.href = `#testcases/${encodeURIComponent(tc.id)}`;
    a.innerHTML =
      `<div class="tc-id">${esc(tc.id)}</div>
       <div class="tags">
         <span class="tag cat ${catClass(tc.category)}">${esc(tc.category)}</span>
         <span class="tag vf">${esc(tc.verifier_type)}</span>
         <span class="tag files">${tc.files.length} file(s)</span>
       </div>
       <div class="tc-task">${esc(firstLine(tc.task))}</div>`;
    grid.appendChild(a);
  }
}

// ---- Per-testcase detail (view + inline edit when editable) ----
let TC_EDIT_MODE = false;

async function renderTestcaseDetail(id) {
  const box = $("#tc-detail");
  box.innerHTML = '<span class="empty">loading...</span>';
  await loadConfig();
  const tc = await loadTestcaseDetail(id);
  if (!tc || tc.error) {
    box.innerHTML = `<span class="empty">Testcase <b>${esc(id)}</b> not found.</span>`;
    return;
  }
  if (TC_EDIT_MODE && CONFIG.editable) return renderTestcaseEditor(tc);

  let h = `<div class="row-btns" style="justify-content:space-between">
             <h2 style="margin:0">${esc(tc.id)}</h2>`;
  if (CONFIG.editable)
    h += `<button class="primary sm" onclick="enterEdit()">Edit</button>`;
  h += `</div>`;
  h += `<div class="tags" style="margin:8px 0 14px">
    <span class="tag cat ${catClass(tc.category)}">${esc(tc.category)}</span>
    <span class="tag vf">${esc(tc.verifier_type)} · ${esc(tc.verifier_entry)}</span>
    <span class="tag files">${esc(tc.scoring_mode)}</span>
    <span class="tag files">${esc(tc.source_kind)}${tc.source_repo ? " · " + esc(tc.source_repo) : ""}</span>
  </div>`;
  h += `<h2>Task</h2><div class="task-box">${esc(tc.task)}</div>`;
  if (tc.provenance && Object.keys(tc.provenance).length) {
    h += `<h2>Provenance</h2><div class="kv">`;
    for (const [k, v] of Object.entries(tc.provenance))
      h += `<div class="k">${esc(k)}</div><div>${esc(v)}</div>`;
    h += `</div>`;
  }
  const fcs = tc.file_contents || [];
  h += `<h2>Files (${fcs.length})</h2>`;
  for (const f of fcs) {
    const meta = `${f.size} byte(s)${f.is_text ? "" : " · binary"}`;
    h += `<details class="file-block"><summary><span>${esc(f.path)}</span><small>${esc(meta)}</small></summary>`;
    h += f.is_text ? `<pre class="file-pre">${esc(f.content || "")}</pre>`
                   : `<div class="file-note">Binary file content is not displayed.</div>`;
    h += `</details>`;
  }
  box.innerHTML = h;
}

function enterEdit() { TC_EDIT_MODE = true; renderTestcaseDetail(CURRENT_TC_ID); }
function exitEdit() { TC_EDIT_MODE = false; renderTestcaseDetail(CURRENT_TC_ID); }
let CURRENT_TC_ID = null;

function renderTestcaseEditor(tc) {
  const box = $("#tc-detail");
  const en = CONFIG.enums || {};
  const opts = (arr, cur) => (arr || []).map(v =>
    `<option value="${esc(v)}"${v === cur ? " selected" : ""}>${esc(v)}</option>`).join("");
  let h = `<div class="row-btns" style="justify-content:space-between">
    <h2 style="margin:0">Editing ${esc(tc.id)}</h2>
    <button class="sm" onclick="exitEdit()">Done</button></div>`;

  // Metadata form (writes testcase.toml). Fields map to manifest sections.
  h += `<h2 style="margin-top:12px">Manifest (testcase.toml)</h2>
    <div class="edit-form">
      <label>category<select id="ed-category">${opts(en.categories, tc.category)}</select></label>
      <label>verifier type<select id="ed-verifier">${opts(en.verifier_types, tc.verifier_type)}</select></label>
      <label>verifier entry<input id="ed-entry" value="${esc(tc.verifier_entry)}"></label>
      <label>scoring mode<select id="ed-scoring">${opts(en.scoring_modes, tc.scoring_mode)}</select></label>
      <label>source_kind<select id="ed-source">${opts(en.source_kinds, tc.source_kind)}</select></label>
      <label>source_repo<input id="ed-repo" value="${esc(tc.source_repo || "")}"></label>
      <label>task<textarea id="ed-task">${esc(tc.task)}</textarea></label>
      <div class="row-btns">
        <button class="primary" onclick="saveManifest('${esc(tc.id)}')">Save manifest</button>
        <span id="ed-manifest-note" class="save-note"></span>
      </div>
    </div>`;

  // Per-file editors (writes each text file).
  const fcs = tc.file_contents || [];
  h += `<h2>Files (${fcs.length})</h2>`;
  for (const f of fcs) {
    const fid = "f_" + btoa(unescape(encodeURIComponent(f.path))).replace(/[^a-z0-9]/gi, "");
    const meta = `${f.size} byte(s)${f.is_text ? "" : " · binary"}`;
    h += `<details class="file-block"><summary>
            <span>${esc(f.path)}</span>
            <span class="file-actions">
              <small>${esc(meta)}</small>
              <button class="danger sm" onclick="event.preventDefault();deleteFile('${esc(tc.id)}','${esc(f.path)}')">Delete</button>
            </span></summary>`;
    if (f.is_text) {
      h += `<textarea class="file-edit-area" id="${fid}">${esc(f.content || "")}</textarea>
            <div class="row-btns" style="padding:8px 10px">
              <button class="primary sm" onclick="saveFile('${esc(tc.id)}','${esc(f.path)}','${fid}')">Save</button>
              <span id="${fid}_note" class="save-note"></span>
            </div>`;
    } else {
      h += `<div class="file-note">Binary file — not editable here.</div>`;
    }
    h += `</details>`;
  }

  // Add-a-new-file row
  h += `<h2>Add file</h2>
    <div class="edit-form">
      <label>relative path (e.g. baseline/scripts/x.gd)<input id="ed-newpath" placeholder="baseline/..."></label>
      <div class="row-btns">
        <button class="primary" onclick="addFile('${esc(tc.id)}')">Create file</button>
        <span id="ed-newfile-note" class="save-note"></span>
      </div>
    </div>`;
  box.innerHTML = h;
}

// Build a testcase.toml string from the editor fields and save it.
// NOTE: this whole HTML is a Python raw string delimited by triple quotes, so
// source here must never contain three consecutive double-quote characters.
// Q3 builds the TOML triple-quote delimiter at runtime instead.
function saveManifest(id) {
  const q = s => $(s).value;
  const esc3 = s => String(s).replace(/\\/g, "\\\\").replace(/"/g, '\\"');
  const Q3 = String.fromCharCode(34, 34, 34);  // TOML multiline delimiter
  const repo = q("#ed-repo");
  const lines = [
    "[testcase]",
    'id = "' + esc3(id) + '"',
    'category = "' + esc3(q("#ed-category")) + '"',
    'source_kind = "' + esc3(q("#ed-source")) + '"',
  ];
  if (repo) lines.push('source_repo = "' + esc3(repo) + '"');
  lines.push("task = " + Q3);
  lines.push(q("#ed-task"));
  lines.push(Q3);
  lines.push("");
  lines.push("[verifier]");
  lines.push('type = "' + esc3(q("#ed-verifier")) + '"');
  lines.push('entry = "' + esc3(q("#ed-entry")) + '"');
  lines.push("");
  lines.push("[scoring]");
  lines.push('mode = "' + esc3(q("#ed-scoring")) + '"');
  lines.push("");
  const toml = lines.join("\n");
  postFile(id, "testcase.toml", toml, "#ed-manifest-note");
}

function saveFile(id, path, fid) {
  postFile(id, path, $("#" + fid).value, "#" + fid + "_note");
}

async function postFile(id, path, content, noteSel) {
  const note = $(noteSel);
  if (note) { note.className = "save-note"; note.textContent = "saving..."; }
  try {
    const res = await fetch("/api/testcase/save-file", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({id, path, content}),
    });
    const j = await res.json();
    if (!res.ok) throw new Error(j.error || ("HTTP " + res.status));
    if (note) { note.className = "save-note ok"; note.textContent = "saved ✓"; }
    TESTCASE_DETAILS.delete(id); TESTCASES = null;  // invalidate caches
  } catch (e) {
    if (note) { note.className = "save-note err"; note.textContent = "error: " + e.message; }
  }
}

async function addFile(id) {
  const path = $("#ed-newpath").value.trim();
  const note = $("#ed-newfile-note");
  if (!path) { note.className = "save-note err"; note.textContent = "enter a path"; return; }
  await postFile(id, path, "", "#ed-newfile-note");
  TESTCASE_DETAILS.delete(id);
  await loadTestcaseDetail(id);
  renderTestcaseDetail(id);
}

async function deleteFile(id, path) {
  if (!confirm(`Delete ${path} from ${id}?`)) return;
  try {
    const res = await fetch("/api/testcase/delete-file", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({id, path}),
    });
    const j = await res.json();
    if (!res.ok) throw new Error(j.error || ("HTTP " + res.status));
    TESTCASE_DETAILS.delete(id); TESTCASES = null;
    await loadTestcaseDetail(id);
    renderTestcaseDetail(id);
  } catch (e) { alert("Delete failed: " + e.message); }
}

async function createTestcase() {
  const en = (CONFIG && CONFIG.enums) || {};
  const cats = en.categories || ["behavior_logic"];
  const id = prompt("New testcase id (lowercase, digits, '-' or '_'):");
  if (!id) return;
  const category = prompt("Category (" + cats.join(" / ") + "):", "behavior_logic");
  if (!category) return;
  const task = prompt("Task (what should the AI do?):", "") || "";
  try {
    const res = await fetch("/api/testcase/create", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({id, category, task}),
    });
    const j = await res.json();
    if (!res.ok) throw new Error(j.error || ("HTTP " + res.status));
    TESTCASES = null; TESTCASE_DETAILS.delete(id);
    TC_EDIT_MODE = true;
    location.hash = "testcases/" + encodeURIComponent(id);
  } catch (e) { alert("Create failed: " + e.message); }
}

async function renderContentsList(selectedId=null) {
  const list = $("#content-list");
  list.innerHTML = '<span class="empty">loading...</span>';
  await loadTestcases();
  if (!TESTCASES.length) {
    list.innerHTML = '<span class="empty">No testcases.<br>Run serve with --testcases-dir.</span>';
    $("#content-detail").innerHTML = '<span class="empty">No testcase content.</span>';
    return;
  }
  const current = TESTCASES.some(t => t.id === selectedId) ? selectedId : TESTCASES[0].id;
  list.innerHTML = "";
  for (const tc of TESTCASES) {
    const a = document.createElement("a");
    a.className = "content-item" + (tc.id === current ? " active" : "");
    a.href = `#contents/${encodeURIComponent(tc.id)}`;
    a.innerHTML = `<span>${esc(tc.id)}</span>
      <small>${esc(tc.category)} - ${tc.files.length} file(s)</small>`;
    list.appendChild(a);
  }
  renderContentsDetail(current);
}

async function loadTestcaseDetail(id) {
  if (TESTCASE_DETAILS.has(id)) return TESTCASE_DETAILS.get(id);
  const res = await fetch(`/api/testcase?id=${encodeURIComponent(id)}`);
  if (!res.ok) return null;
  const tc = await res.json();
  TESTCASE_DETAILS.set(id, tc);
  return tc;
}

async function renderContentsDetail(id) {
  const box = $("#content-detail");
  box.innerHTML = '<span class="empty">loading content...</span>';
  const tc = await loadTestcaseDetail(id);
  if (!tc) {
    box.innerHTML = `<span class="empty">Testcase <b>${esc(id)}</b> not found.</span>`;
    return;
  }
  let h = `<h2>${esc(tc.id)}</h2>`;
  h += `<div class="kv">
    <div class="k">category</div><div><span class="pill">${esc(tc.category)}</span></div>
    <div class="k">verifier</div><div>${esc(tc.verifier_type)} <small>(${esc(tc.verifier_entry)})</small></div>
    <div class="k">scoring</div><div>${esc(tc.scoring_mode)}</div>
  </div>`;
  h += `<h2>Task</h2><div class="task-box">${esc(tc.task)}</div>`;
  h += `<h2>Files (${(tc.file_contents || []).length})</h2>`;
  for (const f of (tc.file_contents || [])) {
    const meta = `${f.size} byte(s)${f.is_text ? "" : " - binary"}`;
    h += `<details class="file-block" open><summary><span>${esc(f.path)}</span><small>${esc(meta)}</small></summary>`;
    if (f.is_text) {
      h += `<pre class="file-pre">${esc(f.content || "")}</pre>`;
    } else {
      h += `<div class="file-note">Binary file content is not displayed.</div>`;
    }
    h += `</details>`;
  }
  box.innerHTML = h;
}

// hash router: ""/"#reports" -> reports, "#testcases" -> list,
// "#testcases/<id>" -> that testcase's sub-page, "#contents" -> full contents.
function showView(which) {
  const views = {reports:"#view-reports", testcases:"#view-testcases",
                 testcase:"#view-testcase", contents:"#view-contents",
                 run:"#view-run", status:"#view-status", webhooks:"#view-webhooks"};
  for (const [k, sel] of Object.entries(views))
    $(sel).style.display = (k === which) ? "" : "none";
  $("#tab-reports").classList.toggle("active", which === "reports");
  $("#tab-testcases").classList.toggle("active", which === "testcases" || which === "testcase");
  $("#tab-contents").classList.toggle("active", which === "contents");
  $("#tab-run").classList.toggle("active", which === "run");
  $("#tab-status").classList.toggle("active", which === "status");
  $("#tab-webhooks").classList.toggle("active", which === "webhooks");
}

function route() {
  const h = location.hash.replace(/^#/, "");
  if (h.startsWith("testcases/")) {
    showView("testcase");
    CURRENT_TC_ID = decodeURIComponent(h.slice("testcases/".length));
    renderTestcaseDetail(CURRENT_TC_ID);
  } else if (h === "testcases") {
    showView("testcases");
    TC_EDIT_MODE = false;  // leaving a detail page always resets edit mode
    renderTestcaseList();
  } else if (h.startsWith("contents/")) {
    showView("contents");
    renderContentsList(decodeURIComponent(h.slice("contents/".length)));
  } else if (h === "contents") {
    showView("contents");
    renderContentsList();
  } else if (h === "run") {
    showView("run");
    enterRunTab();
  } else if (h === "status") {
    showView("status");
    enterStatusTab();
  } else if (h === "webhooks") {
    showView("webhooks");
    enterWebhooksTab();
  } else {
    showView("reports");
  }
}
window.addEventListener("hashchange", route);

$("#tab-reports").addEventListener("click", () => { location.hash = "reports"; });
$("#tab-testcases").addEventListener("click", () => { location.hash = "testcases"; });
$("#tab-contents").addEventListener("click", () => { location.hash = "contents"; });
$("#tab-run").addEventListener("click", () => { location.hash = "run"; });
$("#tab-status").addEventListener("click", () => { location.hash = "status"; });
$("#tab-webhooks").addEventListener("click", () => { location.hash = "webhooks"; });
$("#tc-back").addEventListener("click", () => { location.hash = "testcases"; });

// Testcases toolbar: live search + New button
const tcSearchEl = $("#tc-search");
if (tcSearchEl) tcSearchEl.addEventListener("input", e => {
  TC_SEARCH = e.target.value; renderTestcaseList();
  // keep focus after re-render is not needed: we only re-render the grid/chips
});
const tcNewEl = $("#tc-new");
if (tcNewEl) tcNewEl.addEventListener("click", createTestcase);

$("#refresh").addEventListener("click", () => {
  TESTCASES = null; TESTCASE_DETAILS = new Map(); CONFIG = null; load();
  if (location.hash.startsWith("#testcases") || location.hash.startsWith("#contents")) route();
});
$("#close").addEventListener("click", () => $("#overlay").classList.remove("show"));
$("#overlay").addEventListener("click", e => {
  if (e.target.id === "overlay") $("#overlay").classList.remove("show"); });
document.addEventListener("keydown", e => {
  if (e.key === "Escape") $("#overlay").classList.remove("show"); });

// ---- Run tab: launch a real docker+k8s matrix and stream its status ----
let RUN_POLL = null;
let RUN_INIT = false;

function renderRunInfra() {
  const r = (CONFIG && CONFIG.run) || {};
  const rows = [
    ["executor", "docker build/push skipped · one k8s Job per testcase"],
    ["runner image", r.runner_image || "(default)"],
    ["k8s namespace", r.namespace || "default"],
    ["harness secret", r.harness_secret || "aigdbench-harness"],
    ["testcases (in image)", r.image_testcases_dir || ""],
    ["ids from", r.local_testcases_dir || "(none — blank runs all discovered)"],
  ];
  $("#run-infra").innerHTML = rows.map(
    ([k, v]) => `<span class="k">${esc(k)}</span><span class="v">${esc(v)}</span>`
  ).join("");
  if (r.default_jobs && !$("#run-jobs").dataset.userset)
    $("#run-jobs").value = r.default_jobs;
}

async function enterRunTab() {
  if (!CONFIG) await loadConfig();
  const disabled = !(CONFIG && CONFIG.allow_run);
  $("#run-disabled").style.display = disabled ? "" : "none";
  $("#run-form").style.display = disabled ? "none" : "";
  $("#run-intro").style.display = disabled ? "none" : "";
  $("#run-infra").style.display = disabled ? "none" : "";
  $("#run-start").disabled = disabled;
  if (disabled) return;
  if (!RUN_INIT) {
    RUN_INIT = true;
    $("#run-jobs").addEventListener("input", e => { e.target.dataset.userset = "1"; });
    $("#run-start").addEventListener("click", startRun);
    $("#run-stop").addEventListener("click", stopRun);
  }
  renderRunInfra();
  loadRunImages();
  refreshRunStatus();
}

// Populate the runner-image dropdown with tags from beaver_hub-public (Harbor).
async function loadRunImages() {
  const sel = $("#run-image");
  try {
    const d = await (await fetch("/api/images")).json();
    const tags = (d && d.tags) || [];
    if (!tags.length) {
      sel.innerHTML = '<option value="">' +
        (d && d.error ? "error: " + esc(d.error) : "no images") + '</option>';
      return;
    }
    const def = (d && d.default) || "";
    sel.innerHTML = tags.map(t => {
      const label = t.tag + (t.pushed ? "  (" + t.pushed.replace("T", " ") + ")" : "")
        + (t.tag === def ? "  — default" : "");
      return `<option value="${esc(t.tag)}"${t.tag === def ? " selected" : ""}>${esc(label)}</option>`;
    }).join("");
  } catch (e) {
    sel.innerHTML = '<option value="">failed to load images</option>';
  }
}

async function startRun() {
  const name = $("#run-name").value.trim();
  const msg = $("#run-msg");
  if (!name) {
    msg.className = "run-msg err"; msg.textContent = "run name is required";
    return;
  }
  const payload = {
    name: name,
    image: $("#run-image").value,
    testcases: $("#run-testcases").value.trim(),
    harness_cmd: $("#run-harness").value.trim(),
    jobs: $("#run-jobs").value,
    timeout: $("#run-timeout").value,
  };
  msg.className = "run-msg"; msg.textContent = "starting docker/k8s matrix...";
  $("#run-start").disabled = true;
  try {
    const res = await fetch("/api/runs/start", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok) {
      msg.className = "run-msg err"; msg.textContent = data.error || "failed";
      $("#run-start").disabled = false;
      return;
    }
    msg.textContent = "started";
    applyRunStatus(data);
    beginRunPolling();
  } catch (err) {
    msg.className = "run-msg err"; msg.textContent = String(err);
    $("#run-start").disabled = false;
  }
}

async function stopRun() {
  $("#run-stop").disabled = true;
  try {
    const res = await fetch("/api/runs/stop", {method: "POST"});
    applyRunStatus(await res.json());
  } catch (err) { /* ignore */ }
}

async function refreshRunStatus() {
  try {
    const res = await fetch("/api/runs/status");
    const data = await res.json();
    applyRunStatus(data);
    if (data.state === "running") beginRunPolling();
  } catch (err) { /* ignore */ }
}

function beginRunPolling() {
  if (RUN_POLL) return;
  RUN_POLL = setInterval(async () => {
    try {
      const res = await fetch("/api/runs/status");
      applyRunStatus(await res.json());
    } catch (err) { /* ignore */ }
  }, 2000);
}

function stopRunPolling() {
  if (RUN_POLL) { clearInterval(RUN_POLL); RUN_POLL = null; }
}

let RUN_LAST_STATE = null;
function applyRunStatus(s) {
  if (!s || s.state === "disabled") return;
  const badge = $("#run-badge");
  const labels = {running:"running", done:"done", failed:"failed", idle:"idle"};
  badge.className = "badge " + (s.state === "running" ? "run" : s.state);
  badge.textContent = labels[s.state] || s.state;
  $("#run-elapsed").textContent = s.elapsed ? (s.elapsed + "s") : "";
  $("#run-report").textContent = s.report_file
    ? (s.report_ready ? ("report: " + s.report_file)
                      : ("report (pending): " + s.report_file)) : "";
  const metaBits = [];
  if (s.name) metaBits.push("name=" + s.name);
  if (s.harness_cmd) metaBits.push("harness=" + s.harness_cmd);
  if (s.image) metaBits.push("image=" + s.image);
  if (s.namespace) metaBits.push("ns=" + s.namespace);
  if (s.testcase_count) metaBits.push(s.testcase_count + " testcase(s)");
  if (s.error) metaBits.push("⚠ " + s.error);
  $("#run-meta").textContent = metaBits.join("  ·  ");
  $("#run-cmdline").textContent = s.cmd || "";
  if (s.log_tail) $("#run-log").textContent = s.log_tail;
  const running = s.state === "running";
  $("#run-start").disabled = running || !(CONFIG && CONFIG.allow_run);
  $("#run-stop").disabled = !running;
  if (running) {
    beginRunPolling();
  } else {
    stopRunPolling();
    // A run just finished: refresh Reports data so the new report shows.
    if (RUN_LAST_STATE === "running") {
      const m = $("#run-msg");
      m.className = "run-msg";
      m.textContent = "finished (" + s.state + ")"
        + (s.report_ready ? " — see Reports tab" : "");
      load();
    }
  }
  RUN_LAST_STATE = s.state;
}

// ---- Webhooks tab: show deliveries received by bench-orchestrator.sh ----
let WH_POLL = null;
let WH_INIT = false;
let WH_ALL = false;   // true once "Load all" is clicked -> fetch full history

function whBadge(h) {
  if (h.decision === "accepted") return '<span class="badge b-accepted">accepted</span>';
  if (h.decision === "skipped")  return '<span class="badge b-skipped">skipped'
    + (h.skipped_reason ? " · " + esc(h.skipped_reason) : "") + '</span>';
  if (h.decision === "error")    return '<span class="badge b-error">error'
    + (h.error ? " · " + esc(h.error) : "") + '</span>';
  return '<span class="badge b-event">' + esc(h.decision || "received") + '</span>';
}

function whTime(ts) {
  if (!ts) return "";
  try { return new Date(ts * 1000).toLocaleString(); } catch (e) { return String(ts); }
}

function renderWebhooks(hooks, total) {
  const list = $("#wh-list");
  const t = (typeof total === "number") ? total : hooks.length;
  $("#wh-count").textContent = (hooks.length < t)
    ? `showing ${hooks.length} of ${t}` : `${t} received`;
  // Hide "Load all" once everything is shown.
  const btn = $("#wh-loadall");
  if (btn) btn.style.display = (hooks.length < t) ? "" : "none";
  if (!hooks.length) {
    list.innerHTML = '<span class="empty">No webhooks received yet.</span>';
    return;
  }
  list.innerHTML = hooks.map((h, i) => {
    const open = i === 0 ? " open" : "";
    const bodyStr = (typeof h.body === "object")
      ? JSON.stringify(h.body, null, 2) : esc(h.body);
    return `<details class="hook"${open}>
      <summary>
        ${whBadge(h)}
        <span class="badge b-event">${esc(h.event || "?")}${h.action ? " / " + esc(h.action) : ""}</span>
        <span class="id">${esc(h.delivery || "(no delivery id)")}</span>
        <span class="from">from ${esc(h.client || "?")} · ${esc(h.source || "http")}</span>
        <span style="flex:1"></span>
        <span class="when">${esc(whTime(h.time))}</span>
      </summary>
      <div class="body">
        <div class="kv">
          <span class="k">repo</span><span>${esc(h.repo || "")}</span>
          <span class="k">pr_number</span><span>${esc(h.pr_number || "")}</span>
          <span class="k">head_sha</span><span>${esc(h.head_sha || "")}</span>
          <span class="k">base_ref</span><span>${esc(h.base_ref || "")}</span>
        </div>
        <pre>${esc(bodyStr)}</pre>
      </div>
    </details>`;
  }).join("");
}

async function loadWebhooks() {
  try {
    const q = WH_ALL ? "?limit=all" : "";
    const d = await (await fetch("/api/webhooks" + q)).json();
    if (d && d.disabled) {
      $("#wh-disabled").style.display = "";
      $("#wh-list").innerHTML = "";
      $("#wh-count").textContent = "";
      const btn = $("#wh-loadall"); if (btn) btn.style.display = "none";
      return;
    }
    $("#wh-disabled").style.display = "none";
    renderWebhooks((d && d.webhooks) || [], d && d.total);
  } catch (e) {
    $("#wh-list").innerHTML = '<span class="empty">Failed to load: ' + esc(e) + '</span>';
  }
}

function whSetAuto(on) {
  if (WH_POLL) { clearInterval(WH_POLL); WH_POLL = null; }
  if (on) WH_POLL = setInterval(loadWebhooks, 3000);
}

function enterWebhooksTab() {
  if (!WH_INIT) {
    WH_INIT = true;
    $("#wh-refresh").addEventListener("click", loadWebhooks);
    $("#wh-loadall").addEventListener("click", () => { WH_ALL = true; loadWebhooks(); });
    $("#wh-auto").addEventListener("change", e => whSetAuto(e.target.checked));
  }
  loadWebhooks();
  whSetAuto($("#wh-auto").checked);
}

// ---- Status tab + always-on header indicator ----
// A single background poller (started once) drives BOTH the header live dot
// (visible on every tab) and the Status tab detail. It polls faster while a
// run is active, slower when idle, so an in-flight benchmark is always visible.
let ST_TIMER = null;
let ST_LAST = null;

function renderLiveIndicator(s) {
  const el = $("#live-indicator");
  if (!el) return;
  if (!s || s.state === "disabled" || s.state === "idle") {
    // Only keep showing a finished run's dot; hide when never run / idle.
    if (!s || s.state === "idle" || s.state === "disabled") {
      el.className = "live-indicator"; el.innerHTML = ""; el.classList.remove("show");
      return;
    }
  }
  const p = s.progress || {};
  let text;
  if (s.state === "running") {
    text = `running ${p.completed||0}/${p.total||0}`;
  } else if (s.state === "done") {
    text = `done ${p.completed||0}/${p.total||0}`;
  } else if (s.state === "failed") {
    text = "run failed";
  } else { text = s.state; }
  el.className = "live-indicator show " + s.state;
  el.innerHTML = `<span class="dot"></span><span>${esc(s.name ? s.name+": " : "")}${esc(text)}</span>`;
}

function renderStatusView(s) {
  const badge = $("#st-badge");
  const p = (s && s.progress) || {total:0, completed:0, percent:0, results:[]};
  const st = s ? s.state : "idle";
  badge.className = "badge " + (st === "running" ? "run" : st);
  badge.textContent = st;
  $("#st-title").textContent = (s && s.name)
    ? (st === "running" ? `Running “${s.name}”` : `“${s.name}” — ${st}`)
    : "No benchmark running";
  $("#st-elapsed").textContent = (s && s.elapsed) ? (s.elapsed + "s") : "";
  const pct = p.percent || 0;
  $("#st-bar-fill").style.width = pct + "%";
  $("#st-bar-num").textContent = `${p.completed||0} / ${p.total||0}` +
    (p.total ? `  (${pct}%)` : "");
  const meta = [];
  if (s && s.external) meta.push("source=webhook PR candidate");
  if (s && s.phase) meta.push("phase=" + s.phase);
  if (s && s.image) meta.push("image=" + s.image);
  if (s && s.namespace) meta.push("ns=" + s.namespace);
  if (s && s.report_ready && s.report_file) meta.push("report=" + s.report_file);
  if (s && s.error) meta.push("⚠ " + s.error);
  $("#st-meta").textContent = meta.join("  ·  ");

  // Per-testcase grid: show every planned id, mark done ones with their score.
  const grid = $("#st-grid");
  const ids = (s && s.testcases) || [];
  const byId = {};
  for (const r of (p.results || [])) byId[r.testcase_id] = r;
  const list = ids.length ? ids : (p.results || []).map(r => r.testcase_id);
  if (!list.length) {
    grid.innerHTML = '<span class="empty">No run yet. Start one from the Run tab.</span>';
    return;
  }
  grid.innerHTML = list.map(id => {
    const r = byId[id];
    let cls = "pending", sc = "…";
    if (r) {
      const passed = (typeof r.score === "number") ? r.score >= 1.0 : false;
      cls = (r.status === "pass" || passed) ? "pass"
          : (r.status === "error" || r.status === "fail" || r.score === 0) ? "fail" : "pass";
      sc = (typeof r.score === "number") ? r.score.toFixed(2) : (r.status || "done");
    } else if (st === "running") {
      cls = "run"; sc = "running";
    }
    return `<div class="st-cell ${cls}"><span class="nm">${esc(id)}</span>` +
           `<span class="sc">${esc(sc)}</span></div>`;
  }).join("");
}

async function pollStatus() {
  try {
    const s = await (await fetch("/api/runs/status")).json();
    ST_LAST = s;
    renderLiveIndicator(s);
    // Only repaint the Status view when it's the visible tab (cheap guard).
    if (location.hash.replace(/^#/, "") === "status") renderStatusView(s);
    // A run just finished -> refresh Reports so the new report shows.
    if (window.__ST_PREV === "running" && s.state !== "running") load();
    window.__ST_PREV = s.state;
    // Adapt cadence: fast while running, slow when idle.
    const want = (s.state === "running") ? 2000 : 8000;
    if (ST_TIMER && ST_TIMER._ms !== want) {
      clearInterval(ST_TIMER); ST_TIMER = null;
    }
    if (!ST_TIMER) { ST_TIMER = setInterval(pollStatus, want); ST_TIMER._ms = want; }
  } catch (e) { /* ignore transient errors */ }
}

function enterStatusTab() {
  renderStatusView(ST_LAST);   // paint immediately from last known
  pollStatus();                // then refresh
}

// Kick off the background poller once (drives the header dot on every tab),
// but only if the Run feature is enabled.
async function initStatusPoller() {
  if (!CONFIG) await loadConfig();
  if (CONFIG && CONFIG.allow_run) pollStatus();
}

load();
route();
initStatusPoller();
</script>
</body>
</html>
"""


def load_webhooks(log_path: Path, limit: int | None = 500) -> tuple[list[dict], int]:
    """Read a JSONL webhook log (written by the webhook receiver), newest first.

    Returns (rows, total) where total is the count of all valid records in the
    file and rows is the newest `limit` of them (or all when limit is None).
    Each line is one received webhook delivery. Tolerates partial/garbage lines
    and a missing file (returns ([], 0)). The full history is always kept in the
    file; the dashboard can load a slice or everything via the Webhooks tab.
    """
    log_path = Path(log_path)
    if not log_path.exists():
        return [], 0
    out: list[dict] = []
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                if isinstance(obj, dict):
                    out.append(obj)
    except OSError:
        return [], 0
    total = len(out)
    out.reverse()
    if limit is not None:
        out = out[:limit]
    return out, total


def load_reports(reports_dir: Path) -> list[dict]:
    """Load every benchmark report JSON in `reports_dir`.

    Each loaded report gets three derived fields injected:
      _file:   the filename (used as the stable, unique run discriminator)
      _mtime:  file modification time (reports carry no timestamp of their own)
      _run_id: a unique id for the run, derived from harness + filename so two
               runs of the same harness still get distinct ids.
    Files that are not valid JSON, or are JSON without a `testcases` list, are
    skipped so one stray file cannot break the whole view.
    """
    reports_dir = Path(reports_dir)
    out: list[dict] = []
    for path in sorted(reports_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(data, dict) or not isinstance(data.get("testcases"), list):
            continue
        data["_file"] = path.name
        data["_mtime"] = path.stat().st_mtime
        harness = data.get("harness") or "run"
        data["_run_id"] = f"{harness}::{path.stem}"
        out.append(data)
    return out


def build_summary(reports: list[dict]) -> dict:
    """Aggregate loaded reports into the structure the web UI consumes."""
    runs = []
    categories: dict[str, dict] = {}
    timing: dict[str, dict] = {}
    failure_stages: dict[str, dict] = {}
    testcase_ids: set[str] = set()
    matrix: dict[str, dict] = {}

    for r in reports:
        run_id = r["_run_id"]
        tcs = r.get("testcases", [])
        runs.append({
            "run_id": run_id,
            "file": r.get("_file", ""),
            "harness": r.get("harness", ""),
            "mtime": r.get("_mtime", 0.0),
            "count": r.get("count", len(tcs)),
            "mean_score": r.get("mean_score", 0.0),
            # Repeat metadata (present when the run used --repeat N): the suite
            # confidence interval and N, so the UI can show mean ± CI and flag
            # single-sample runs as having no variance information.
            "repeat": r.get("repeat", 1),
            "mean_score_ci95": r.get("mean_score_ci95"),
        })
        categories[run_id] = _category_aggregate(tcs)
        timing[run_id] = _timing_aggregate(tcs)
        failure_stages[run_id] = _failure_stage_aggregate(tcs)
        for tc in tcs:
            testcase_ids.add(tc["testcase_id"])

    ordered_ids = sorted(testcase_ids)
    run_ids = [run["run_id"] for run in runs]
    for tcid in ordered_ids:
        matrix[tcid] = {run_id: None for run_id in run_ids}
    for r in reports:
        run_id = r["_run_id"]
        for tc in r.get("testcases", []):
            matrix[tc["testcase_id"]][run_id] = tc.get("score")

    return {
        "runs": runs,
        "testcases": ordered_ids,
        "matrix": matrix,
        "categories": categories,
        "timing": timing,
        "failure_stages": failure_stages,
    }


def _category_aggregate(testcases: list[dict]) -> dict:
    by_cat: dict[str, list[dict]] = {}
    for tc in testcases:
        by_cat.setdefault(tc.get("category", ""), []).append(tc)
    out = {}
    for cat, items in by_cat.items():
        n = len(items)
        mean = sum(t.get("score", 0.0) for t in items) / n if n else 0.0
        passed = sum(1 for t in items if t.get("score", 0.0) >= 1.0)
        out[cat] = {
            "mean": mean,
            "pass_rate": passed / n if n else 0.0,
            "count": n,
        }
    return out


def _timing_aggregate(testcases: list[dict]) -> dict:
    walls = [t["wall_time"] for t in testcases if isinstance(t.get("wall_time"), (int, float))]
    total = sum(walls)

    # Per-stage pipeline timings (ms), averaged over the runs that recorded them.
    # These come from RunResult.timings and expose where the non-harness time
    # goes (godot import, L0 boot, verifier) — the cost side of the
    # cost/correctness tradeoff, which wall_time alone hides.
    stage_keys = ("import_ms", "l0_ms", "l1_ms", "verifier_ms", "total_ms")
    stage_means: dict[str, float] = {}
    for key in stage_keys:
        vals = [t["timings"][key] for t in testcases
                if isinstance(t.get("timings"), dict)
                and isinstance(t["timings"].get(key), (int, float))]
        if vals:
            stage_means[key] = sum(vals) / len(vals)

    return {
        "total_wall_time": total,
        "mean_wall_time": total / len(walls) if walls else 0.0,
        "timed_out": sum(1 for t in testcases if t.get("timed_out")),
        "stalled": sum(1 for t in testcases if t.get("stalled")),
        "blocked_on_approval": sum(1 for t in testcases if t.get("blocked_on_approval")),
        "stage_means": stage_means,
    }


def _failure_stage_aggregate(testcases: list[dict]) -> dict:
    """Count runs by the pipeline layer that explains their outcome. A failure
    funnel: no_change / harness_error / l0 / l1 / verifier are losses at each
    layer; `none` means the verifier ran (score may still be < 1 — a capability
    gap, not a pipeline failure)."""
    counts: dict[str, int] = {}
    for t in testcases:
        stage = t.get("failure_stage", "none")
        counts[stage] = counts.get(stage, 0) + 1
    return counts


def load_testcase_catalog(testcases_dir: Path) -> list[dict]:
    """Load every testcase under `testcases_dir` into a JSON-friendly catalog.

    Each entry carries the manifest fields (id/category/task/verifier/scoring)
    plus a sorted `files` list (relative paths) so the UI can show what the
    testcase is made of. A directory without a parseable testcase.toml is
    skipped so one broken testcase cannot break the catalog view.
    """
    from aigamedevbench.testcase import load_testcase

    testcases_dir = Path(testcases_dir)
    if not testcases_dir.is_dir():
        return []
    out: list[dict] = []
    for child in sorted(testcases_dir.iterdir()):
        if not child.is_dir() or not (child / "testcase.toml").exists():
            continue
        try:
            tc = load_testcase(child)
        except Exception:
            continue
        files = _testcase_files(child)
        out.append({
            "id": tc.id,
            "category": tc.category,
            "task": tc.task,
            "verifier_type": tc.verifier_type,
            "verifier_entry": tc.verifier_entry,
            "scoring_mode": tc.scoring_mode,
            "source_kind": tc.source_kind,
            "source_repo": tc.source_repo,
            "provenance": tc.provenance,
            "files": files,
        })
    return out


def load_testcase_detail(testcases_dir: Path, testcase_id: str) -> dict | None:
    from aigamedevbench.testcase import load_testcase

    testcases_dir = Path(testcases_dir)
    child = testcases_dir / testcase_id
    if not child.is_dir() or child.parent != testcases_dir or not (child / "testcase.toml").exists():
        return None
    try:
        tc = load_testcase(child)
    except Exception:
        return None
    file_contents = _testcase_file_contents(child)
    return {
        "id": tc.id,
        "category": tc.category,
        "task": tc.task,
        "verifier_type": tc.verifier_type,
        "verifier_entry": tc.verifier_entry,
        "scoring_mode": tc.scoring_mode,
        "source_kind": tc.source_kind,
        "source_repo": tc.source_repo,
        "provenance": tc.provenance,
        "files": [f["path"] for f in file_contents],
        "file_contents": file_contents,
    }


def _testcase_files(testcase_dir: Path) -> list[str]:
    return sorted(p.relative_to(testcase_dir).as_posix()
                  for p in testcase_dir.rglob("*") if p.is_file())


def _testcase_file_contents(testcase_dir: Path) -> list[dict]:
    entries = []
    for path in sorted(p for p in testcase_dir.rglob("*") if p.is_file()):
        rel = path.relative_to(testcase_dir).as_posix()
        try:
            data = path.read_bytes()
        except OSError:
            continue
        content = _decode_text_file(data)
        entries.append({
            "path": rel,
            "size": len(data),
            "is_text": content is not None,
            "content": content,
        })
    return entries


def _decode_text_file(data: bytes) -> str | None:
    if b"\0" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


# --- Write operations (only reachable when the server runs with --editable) ---

class EditError(Exception):
    """A rejected edit request. Carries an HTTP-ish status for the handler."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _safe_testcase_dir(testcases_dir: Path, testcase_id: str) -> Path:
    """Resolve <testcases_dir>/<id>, rejecting anything that escapes the root.

    The id must be a single path segment (no separators, no '..'); the resolved
    directory must sit directly under the resolved testcases root. This is the
    guard that keeps write endpoints from touching files outside the catalog."""
    root = Path(testcases_dir).resolve()
    if not testcase_id or "/" in testcase_id or "\\" in testcase_id or testcase_id in (".", ".."):
        raise EditError(f"invalid testcase id: {testcase_id!r}")
    target = (root / testcase_id).resolve()
    if target.parent != root:
        raise EditError(f"testcase id escapes the catalog: {testcase_id!r}")
    return target


def _safe_file_path(testcases_dir: Path, testcase_id: str, rel_path: str) -> Path:
    """Resolve a file path inside a testcase dir, rejecting traversal."""
    tc_dir = _safe_testcase_dir(testcases_dir, testcase_id)
    if not rel_path or rel_path.startswith(("/", "\\")):
        raise EditError(f"invalid file path: {rel_path!r}")
    target = (tc_dir / rel_path).resolve()
    try:
        target.relative_to(tc_dir)
    except ValueError:
        raise EditError(f"file path escapes the testcase: {rel_path!r}")
    return target


def create_testcase(testcases_dir: Path, testcase_id: str, category: str,
                    task: str) -> dict:
    """Scaffold a new folder-type testcase and return its fresh catalog entry."""
    from aigamedevbench.testcase_scaffold import scaffold_folder_testcase

    _safe_testcase_dir(testcases_dir, testcase_id)  # validate id shape early
    try:
        scaffold_folder_testcase(Path(testcases_dir), testcase_id,
                                 category=category, task=task or "TODO: describe the task")
    except FileExistsError as e:
        raise EditError(str(e), status=409)
    except (OSError, ValueError) as e:
        raise EditError(str(e))
    detail = load_testcase_detail(Path(testcases_dir), testcase_id)
    if detail is None:
        raise EditError("testcase created but could not be reloaded", status=500)
    return detail


def save_testcase_file(testcases_dir: Path, testcase_id: str, rel_path: str,
                       content: str, backup: bool = False) -> dict:
    """Write (create or overwrite) a text file inside a testcase dir."""
    target = _safe_file_path(testcases_dir, testcase_id, rel_path)
    if not _safe_testcase_dir(testcases_dir, testcase_id).is_dir():
        raise EditError(f"testcase not found: {testcase_id}", status=404)
    target.parent.mkdir(parents=True, exist_ok=True)
    if backup and target.exists():
        try:
            backup_path = target.with_suffix(target.suffix + ".bak")
            backup_path.write_bytes(target.read_bytes())
        except OSError:
            pass  # backup is best-effort; never block the save
    try:
        target.write_text(content, encoding="utf-8")
    except OSError as e:
        raise EditError(f"write failed: {e}", status=500)
    return {"ok": True, "path": rel_path, "size": len(content.encode("utf-8"))}


def delete_testcase_file(testcases_dir: Path, testcase_id: str, rel_path: str) -> dict:
    """Delete a single file inside a testcase dir. testcase.toml is protected."""
    if rel_path == "testcase.toml":
        raise EditError("refusing to delete testcase.toml (delete the whole testcase instead)")
    target = _safe_file_path(testcases_dir, testcase_id, rel_path)
    if not target.exists() or not target.is_file():
        raise EditError(f"file not found: {rel_path}", status=404)
    try:
        target.unlink()
    except OSError as e:
        raise EditError(f"delete failed: {e}", status=500)
    return {"ok": True, "deleted": rel_path}


def editor_enums() -> dict:
    """The allowed values the edit UI offers in dropdowns."""
    from aigamedevbench.testcase import CATEGORIES, VERIFIER_TYPES, SOURCE_KINDS
    return {
        "categories": sorted(CATEGORIES),
        "verifier_types": sorted(VERIFIER_TYPES),
        "source_kinds": sorted(SOURCE_KINDS),
        "scoring_modes": ["checkpoints", "fields", "tristate", "weighted"],
    }


LOG_TAIL_BYTES = 64 * 1024


def report_detail(report: dict, testcase_id: str,
                  reports_dir: Path | None = None) -> dict | None:
    """Per-testcase drill-down: checks (with expected/actual), error, diff, and
    the AI's activity record for that run.

    The activity comes from two places depending on how the run was produced:
      - survey reports carry `ai_agent_context.turns` (agent_input/output/
        tool_calls) — surfaced as `ai_turns`.
      - command-harness runs carry a `log_path` to the harness stdout/stderr —
        its tail is read into `log_text` (resolved against `reports_dir`).
    """
    for tc in report.get("testcases", []):
        if tc["testcase_id"] == testcase_id:
            vr = tc.get("verifier_result", {})
            ctx = tc.get("ai_agent_context") or {}
            ai_turns = ctx.get("turns", []) if isinstance(ctx, dict) else []
            log_path = tc.get("log_path")
            return {
                "run_id": report.get("_run_id", ""),
                "testcase_id": testcase_id,
                "category": tc.get("category", ""),
                "score": tc.get("score"),
                "status": vr.get("status", ""),
                "error": vr.get("error", ""),
                "checks": vr.get("checks", []),
                "diff": tc.get("diff", ""),
                "log_path": log_path,
                "log_text": _read_log_tail(log_path, reports_dir),
                "ai_turns": ai_turns,
                "total_tokens": ctx.get("total_tokens") if isinstance(ctx, dict) else None,
                "slowest_turn": ctx.get("slowest_turn") if isinstance(ctx, dict) else None,
                "total_turn_ms": ctx.get("total_turn_ms") if isinstance(ctx, dict) else None,
                "wall_time": tc.get("wall_time"),
                "failure_stage": tc.get("failure_stage", "none"),
                "timings": tc.get("timings") or {},
                "repeat": tc.get("repeat"),
            }
    return None


def _read_log_tail(log_path: str | None, reports_dir: Path | None) -> str:
    """Read the tail of a harness log. log_path may be relative to reports_dir
    (how the runner writes it). Returns "" if there is nothing to read."""
    if not log_path:
        return ""
    p = Path(log_path)
    if not p.is_absolute() and reports_dir is not None:
        p = Path(reports_dir) / p
    try:
        data = p.read_bytes()
    except OSError:
        return ""
    if len(data) > LOG_TAIL_BYTES:
        data = data[-LOG_TAIL_BYTES:]
        prefix = b"...(truncated)...\n"
    else:
        prefix = b""
    return (prefix + data).decode("utf-8", errors="replace")
