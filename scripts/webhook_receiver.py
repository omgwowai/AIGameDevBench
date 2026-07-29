#!/usr/bin/env python3
"""Standalone webhook receiver for the AIGameDevBench dashboard.

Listens for the k8s github-webhook receiver's forwarded deliveries
(POST /trigger), appends each one to a JSONL log that the dashboard's Webhooks
tab reads, and (optionally) launches a benchmark on an opened PR.

Why this exists: the k8s receiver forwards to BENCH_TRIGGER_URL
(e.g. http://<this-host>:8899/trigger). If nothing listens there, the receiver
logs "benchmark trigger forward failed: fetch failed" and the delivery is lost.
Running this keeps the port alive and captures every delivery.

Auto-run modes (per opened PR):
  * candidate (default via start_dashboard.sh) -- run scripts/bench-candidate.sh
    which builds the plugin from origin/main + cherry-pick(PR commit), i.e. the
    plugin AS IF the PR were merged, benchmarks it, and (with --auto-release)
    merges + releases only if it strictly beats the historical best. This is the
    correct "evaluate the merged plugin" behavior.
  * matrix -- POST the dashboard's /api/runs/start to run the shared :latest
    image over all testcases (does NOT reflect the PR's plugin changes).
  * off -- record only.

Usage:
    python3 scripts/webhook_receiver.py --port 8899 \
        --log .orchestrator/webhooks.jsonl [--token <shared-secret>] \
        --autorun-mode candidate --plugin-repo ../agentic-game-development ...

Pure stdlib. The record format matches bench-orchestrator.sh exactly, so the
dashboard renders receiver-captured and orchestrator-captured deliveries the
same way.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def make_handler(log_path: Path, token: str, autorun_mode: str = "off",
                 autorun_url: str = "", autorun_jobs: int = 16,
                 autorun_timeout: int = 2400, candidate_opts: dict | None = None):
    candidate_opts = candidate_opts or {}
    # Single in-flight candidate at a time + de-dupe by head sha across the
    # receiver's lifetime (a PR's redelivery / reopen shouldn't double-run).
    cand_lock = threading.Lock()
    cand_state = {"running": False, "seen": set()}

    dashboard_url = candidate_opts.get("dashboard_url", "")
    # PRs whose head branch starts with this prefix are our own release-bump PRs
    # (opened by bench-candidate.sh to land the version bump on protected main);
    # they must NOT trigger a benchmark. Keep in sync with bench-candidate.sh's
    # BUMP_BRANCH ("release-v...").
    bump_branch_prefix = candidate_opts.get("bump_branch_prefix", "release-v")

    def record(rec: dict) -> None:
        rec.setdefault("time", time.time())
        rec.setdefault("source", "receiver")
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except OSError as e:
            sys.stderr.write(f"[receiver] could not write log: {e}\n")

    def post_external(info: dict) -> None:
        """Tell the dashboard about the externally-launched candidate run so its
        Live status / Status tab can show it (best-effort; never raises)."""
        if not dashboard_url:
            return
        try:
            req = urllib.request.Request(
                dashboard_url, data=json.dumps(info).encode(), method="POST",
                headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=10).read()
        except Exception as e:  # noqa: BLE001
            sys.stderr.write(f"[receiver] external-status post failed: {e}\n")

    def kick_benchmark(delivery: str, pr_number: str, head_sha: str,
                       repo: str) -> None:
        """matrix mode: fire-and-forget POST to /api/runs/start (shared :latest
        image over all testcases). Does NOT reflect the PR's plugin changes."""
        if not autorun_url:
            return
        sha8 = (head_sha or "")[:8]
        name = f"pr-{pr_number or '?'}-{sha8 or delivery[:8]}"
        payload = json.dumps({
            "name": name,
            "testcases": "",          # blank = all filtered testcases
            "jobs": autorun_jobs,
            "timeout": autorun_timeout,
        }).encode()

        def _post():
            try:
                req = urllib.request.Request(
                    autorun_url, data=payload, method="POST",
                    headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=15) as r:
                    body = r.read().decode("utf-8", "replace")
                    sys.stderr.write(
                        f"[receiver] auto-run kicked ({name}): {r.status} {body}\n")
            except urllib.error.HTTPError as e:
                # 409 = a run is already in progress; that's fine.
                msg = e.read().decode("utf-8", "replace") if e.fp else ""
                sys.stderr.write(
                    f"[receiver] auto-run {name}: HTTP {e.code} {msg}\n")
            except Exception as e:  # noqa: BLE001 - never let this kill the handler
                sys.stderr.write(f"[receiver] auto-run {name} failed: {e}\n")

        threading.Thread(target=_post, daemon=True).start()

    def kick_candidate(delivery: str, pr_number: str, head_sha: str,
                       repo: str) -> str:
        """candidate mode: run scripts/bench-candidate.sh for this PR head. It
        builds the plugin from origin/main + cherry-pick(head_sha) (== merged
        plugin), benchmarks it, and with --auto-release merges+releases if it
        beats the historical best. Returns a short status note for the reply."""
        repo_root = Path(candidate_opts["repo_root"])
        script = repo_root / "scripts" / "bench-candidate.sh"
        if not script.is_file():
            sys.stderr.write(f"[receiver] candidate script missing: {script}\n")
            return "candidate script missing"
        key = f"{pr_number}-{head_sha}"
        with cand_lock:
            if key in cand_state["seen"]:
                return "already processed this PR head"
            if cand_state["running"]:
                return "a candidate benchmark is already in progress"
            cand_state["seen"].add(key)
            cand_state["running"] = True

        results_root = Path(candidate_opts["results_root"])
        out_dir = results_root / (delivery or key)
        out_dir.mkdir(parents=True, exist_ok=True)
        cand_log = out_dir / "candidate.log"
        # The matrix writes per-testcase JSONs here (bench-candidate uses
        # results_root/cand-<delivery>); the dashboard scans it for live progress.
        sha8 = (head_sha or "")[:8]
        matrix_out = results_root / f"cand-{delivery or key}"
        run_name = f"pr-{pr_number or '?'}-{sha8 or 'cand'}"
        # Count local testcases so the dashboard progress bar has a denominator.
        total_tc = 0
        ltd = Path(candidate_opts.get("local_testcases_dir", ""))
        if ltd.is_dir():
            total_tc = sum(1 for p in ltd.iterdir()
                           if p.is_dir() and not p.name.startswith("_")
                           and p.name != "README")

        cmd = [
            "bash", str(script),
            "--commit", head_sha,
            "--pr", pr_number or "",
            "--repo", repo or "",
            "--delivery", delivery or key,
            "--plugin-repo", candidate_opts["plugin_repo"],
            "--image-repo", candidate_opts["image_repo"],
            "--secret", candidate_opts["secret"],
            "--jobs", str(autorun_jobs),
            "--timeout", str(autorun_timeout),
            "--testcases-dir", candidate_opts["image_testcases_dir"],
            "--local-testcases-dir", candidate_opts["local_testcases_dir"],
            "--namespace", candidate_opts["namespace"],
            "--results-root", str(results_root),
            "--bump", candidate_opts.get("bump", "patch"),
        ]
        if candidate_opts.get("auto_release"):
            cmd.append("--auto-release")

        def _run():
            post_external({"name": run_name, "state": "running",
                           "phase": "building candidate image",
                           "out_dir": str(matrix_out), "total": total_tc,
                           "namespace": candidate_opts.get("namespace", ""),
                           "image": f"{candidate_opts['image_repo']}:{sha8}",
                           "pr_number": pr_number})
            try:
                with open(cand_log, "wb") as lf:
                    rc = subprocess.call(cmd, cwd=str(repo_root),
                                         stdout=lf, stderr=subprocess.STDOUT)
                sys.stderr.write(
                    f"[receiver] candidate PR#{pr_number} rc={rc} (log {cand_log})\n")
                # Surface the candidate's report in the dashboard Reports tab.
                # bench-candidate writes report.json into the matrix out dir.
                published = _publish_report(matrix_out, pr_number, head_sha)
                post_external({"name": run_name,
                               "state": "done" if rc == 0 else "failed",
                               "phase": "finished",
                               "out_dir": str(matrix_out), "total": total_tc,
                               "pr_number": pr_number,
                               "report_file": published or "",
                               "error": "" if rc == 0 else f"candidate exit {rc}"})
            except Exception as e:  # noqa: BLE001
                sys.stderr.write(f"[receiver] candidate PR#{pr_number} failed: {e}\n")
                post_external({"name": run_name, "state": "failed",
                               "phase": "error", "out_dir": str(matrix_out),
                               "total": total_tc, "pr_number": pr_number,
                               "error": str(e)})
            finally:
                with cand_lock:
                    cand_state["running"] = False

        threading.Thread(target=_run, daemon=True).start()
        return "candidate benchmark launched (main + PR plugin)"

    def _publish_report(out_dir: Path, pr_number: str, head_sha: str) -> str:
        """Copy bench-candidate's report.json into --reports-dir, stamped with a
        pr-<n>-<sha8> harness label so it shows in the Reports tab like any run.
        Returns the published filename (or "" if there was nothing to publish).
        NOTE: bench-candidate writes its report to results_root/cand-<delivery>,
        so out_dir here must be that matrix out dir."""
        reports_dir = candidate_opts.get("reports_dir")
        src = out_dir / "report.json"
        if not reports_dir or not src.is_file():
            return ""
        sha8 = (head_sha or "")[:8]
        name = f"pr-{pr_number or '?'}-{sha8 or 'cand'}"
        try:
            data = json.loads(src.read_text(encoding="utf-8"))
            data["harness"] = name
            data.setdefault("run_name", name)
            data["executor"] = "bench-candidate"
            fname = f"report-{name}.json"
            (Path(reports_dir) / fname).write_text(
                json.dumps(data, indent=2), encoding="utf-8")
            sys.stderr.write(f"[receiver] candidate report published: {fname}\n")
            return fname
        except (OSError, ValueError) as e:
            sys.stderr.write(f"[receiver] could not publish candidate report: {e}\n")
            return ""

    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, obj: dict) -> None:
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):  # keep console quiet
            pass

        def do_GET(self):  # noqa: N802
            if self.path == "/healthz":
                self._send(200, {"ok": True})
            else:
                self._send(404, {"error": "not found"})

        def do_POST(self):  # noqa: N802
            if self.path != "/trigger":
                self._send(404, {"error": "not found"})
                return
            if token and self.headers.get("x-bench-token", "") != token:
                self._send(401, {"error": "bad token"})
                return
            n = int(self.headers.get("Content-Length", "0") or "0")
            raw = self.rfile.read(n) if n > 0 else b"{}"
            client = self.client_address[0]
            try:
                o = json.loads(raw or b"{}")
            except Exception:
                record({"decision": "error", "error": "bad json", "client": client,
                        "event": "?", "body": (raw or b"").decode("utf-8", "replace")})
                self._send(400, {"error": "bad json"})
                return
            if not isinstance(o, dict):
                record({"decision": "error", "error": "body not an object",
                        "client": client, "event": "?", "body": o})
                self._send(400, {"error": "body not an object"})
                return

            delivery = str(o.get("delivery") or "").strip()
            if not delivery:
                record({"decision": "error", "error": "missing delivery",
                        "client": client, "event": str(o.get("event") or "?"),
                        "body": o})
                self._send(400, {"error": "missing delivery"})
                return

            # Mirror bench-orchestrator.sh's event classification exactly.
            event = str(o.get("event") or "").strip().lower()
            pr = o.get("pull_request") or {}
            pr_number = str(o.get("pr_number") or o.get("pr")
                            or pr.get("number") or "").strip()
            head_sha = str(o.get("head_sha")
                           or (pr.get("head") or {}).get("sha") or "").strip()
            base_ref = str(o.get("base_ref")
                           or (pr.get("base") or {}).get("ref") or "main").strip()
            # Head branch name (the PR's source branch). Flat forwards put it in
            # "ref"/"head_ref"; nested payloads under pull_request.head.ref.
            head_ref = str(o.get("head_ref") or o.get("ref")
                           or (pr.get("head") or {}).get("ref") or "").strip()
            action = str(o.get("action") or "").strip().lower()
            is_pr = event in ("pull_request", "pr") or bool(pr_number and head_sha)
            base = {"delivery": delivery, "client": client,
                    "event": event or ("pull_request" if is_pr else "?"),
                    "action": action, "repo": str(o.get("repo")
                                                   or o.get("repository") or ""),
                    "pr_number": pr_number, "head_sha": head_sha,
                    "head_ref": head_ref, "base_ref": base_ref, "body": o}

            if is_pr:
                if action and action != "opened":
                    record({**base, "decision": "skipped", "skipped_reason": action})
                    self._send(202, {"accepted": False, "skipped": action})
                    return
                if not (pr_number and head_sha):
                    record({**base, "decision": "error",
                            "error": "missing pr_number/head_sha"})
                    self._send(400,
                               {"error": "pull_request missing pr_number/head_sha"})
                    return
                # Skip our OWN release-bump PRs: bench-candidate opens a
                # release-v<ver>-<sha> PR to land the version bump on protected
                # main. Benchmarking that would be a wasteful feedback loop (and
                # it just cherry-pick-conflicts). Match by head branch prefix.
                if head_ref and head_ref.startswith(bump_branch_prefix):
                    record({**base, "decision": "skipped",
                            "skipped_reason": "release-bump PR (%s)" % head_ref})
                    self._send(202, {"accepted": False,
                                     "skipped": "release-bump PR",
                                     "head_ref": head_ref})
                    return
                record({**base, "decision": "accepted"})
                note = "recorded (no auto-run)"
                if autorun_mode == "candidate":
                    note = "recorded; " + kick_candidate(
                        delivery, pr_number, head_sha, base["repo"])
                elif autorun_mode == "matrix" and autorun_url:
                    kick_benchmark(delivery, pr_number, head_sha, base["repo"])
                    note = "recorded; benchmark auto-run kicked (matrix :latest)"
                self._send(202, {"accepted": True, "event": "pull_request",
                                 "pr": pr_number, "note": note})
                return

            record({**base, "decision": "skipped",
                    "skipped_reason": event or "non-pull_request"})
            self._send(202, {"accepted": False,
                             "skipped": event or "non-pull_request"})

    return Handler


def main() -> None:
    ap = argparse.ArgumentParser(description="AIGameDevBench webhook receiver")
    ap.add_argument("--port", type=int, default=8899)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--log", required=True,
                    help="JSONL log to append received deliveries to "
                         "(the dashboard reads this via --webhook-log)")
    ap.add_argument("--token", default="",
                    help="Shared x-bench-token to require (empty = no auth)")
    ap.add_argument("--autorun-mode", default="off",
                    choices=["off", "matrix", "candidate"],
                    help="off = record only; matrix = POST /api/runs/start "
                         "(shared :latest image); candidate = run "
                         "scripts/bench-candidate.sh (main + PR merged plugin).")
    ap.add_argument("--autorun-url", default="",
                    help="matrix mode: the dashboard's /api/runs/start URL")
    ap.add_argument("--autorun-jobs", type=int, default=16,
                    help="jobs (max concurrent k8s Jobs) for auto-run")
    ap.add_argument("--autorun-timeout", type=int, default=2400,
                    help="per-testcase timeout (s) for auto-run; the matrix sets "
                         "each Job's activeDeadlineSeconds = timeout + 300")
    # candidate-mode plumbing (passed straight through to bench-candidate.sh):
    ap.add_argument("--repo-root", default=".")
    ap.add_argument("--plugin-repo", default="../agentic-game-development")
    ap.add_argument("--image-repo",
                    default="harbor.omgwow.ai/beaver_hub-public/aigdbench-runner")
    ap.add_argument("--secret", default="aigdbench-harness")
    ap.add_argument("--image-testcases-dir", default="/app/testcases_filtered")
    ap.add_argument("--local-testcases-dir", default="./testcases_filtered")
    ap.add_argument("--namespace", default="default")
    ap.add_argument("--results-root", default="./results")
    ap.add_argument("--reports-dir", default="./dashboard_reports")
    ap.add_argument("--dashboard-url", default="",
                    help="dashboard /api/runs/external URL; when set, candidate "
                         "runs are reported there so the Live status / Status tab "
                         "shows the receiver-launched benchmark")
    ap.add_argument("--bump", default="patch")
    ap.add_argument("--auto-release", action="store_true",
                    help="candidate mode: merge+release if it beats the best")
    args = ap.parse_args()

    log_path = Path(args.log)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.touch(exist_ok=True)

    candidate_opts = {
        "repo_root": args.repo_root,
        "plugin_repo": args.plugin_repo,
        "image_repo": args.image_repo,
        "secret": args.secret,
        "image_testcases_dir": args.image_testcases_dir,
        "local_testcases_dir": args.local_testcases_dir,
        "namespace": args.namespace,
        "results_root": args.results_root,
        "reports_dir": args.reports_dir,
        "dashboard_url": args.dashboard_url,
        "bump": args.bump,
        "auto_release": args.auto_release,
    }

    server = ThreadingHTTPServer(
        (args.host, args.port),
        make_handler(log_path, args.token, autorun_mode=args.autorun_mode,
                     autorun_url=args.autorun_url, autorun_jobs=args.autorun_jobs,
                     autorun_timeout=args.autorun_timeout,
                     candidate_opts=candidate_opts))
    mode_note = {
        "off": "record only",
        "matrix": f"auto-run matrix -> {args.autorun_url}",
        "candidate": "auto-run candidate (main+PR plugin)"
        + (" +auto-release" if args.auto_release else " (gate only)"),
    }.get(args.autorun_mode, args.autorun_mode)
    print(f"[receiver] listening on http://{args.host}:{args.port}/trigger "
          f"-> {log_path}"
          + ("  (token required)" if args.token else "  (no auth)")
          + f"  ({mode_note})", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
