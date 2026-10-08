"""Reproduce evaluation receipt failure modes using owned subprocesses.

This is an independent experiment against the package's public interface.
The artifact format is parsed before converting worker results to a manifest.
"""
from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from evalreceipt import audit_manifest, InputError, load_manifest
from evalreceipt.render import render_html

WORKER = Path(__file__).with_name("worker.py")


def now():
    return datetime.now(timezone.utc).isoformat()


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n", encoding="utf-8")


def reject_constant(value):
    raise ValueError(f"Nonfinite score token {value}")


def execute(directory: Path, mode: str, interpreter: str):
    directory.mkdir(parents=True, exist_ok=False)
    started_at = now()
    output = directory / "prediction.json"
    command = [interpreter, str(WORKER), "--mode", mode, "--out", str(output)]
    timed_out = False
    returncode = None
    start_clock = time.perf_counter()
    try:
        result = subprocess.run(command, text=True, encoding="utf-8", capture_output=True, timeout=0.2 if mode == "timeout" else 20)
        returncode = result.returncode
        stdout, stderr = result.stdout, result.stderr
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        stdout = exc.stdout.decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = exc.stderr.decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
    duration = time.perf_counter() - start_clock
    (directory / "stdout.log").write_text(stdout, encoding="utf-8")
    (directory / "stderr.log").write_text(stderr, encoding="utf-8")
    payload = None
    parse_error = None
    if output.exists():
        try:
            payload = json.loads(output.read_text(encoding="utf-8"), parse_constant=reject_constant)
            if type(payload) is not dict or set(payload) != {"score", "passed", "detail"}:
                raise ValueError("Worker output must contain score, passed and detail")
            score = payload["score"]
            if type(score) not in (int, float) or not math.isfinite(score) or not 0 <= score <= 1:
                raise ValueError("Worker score must be a finite number in [0,1]")
            if type(payload["passed"]) is not bool or payload["passed"] != (score >= 1):
                raise ValueError("Worker pass flag disagrees with the threshold")
        except (ValueError, UnicodeError) as exc:
            parse_error = str(exc)
            payload = None
    artifact = None
    if output.exists():
        artifact = {"path": "prediction.json", "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
                    "created_at": datetime.fromtimestamp(output.stat().st_mtime, timezone.utc).isoformat()}
    record = {"id": "case-1", "status": "timeout" if timed_out else ("completed" if returncode == 0 and payload is not None else "failed"),
              "exit_code": returncode, "timed_out": timed_out,
              "score": payload["score"] if payload else None, "passed": payload["passed"] if payload else None,
              "artifact": artifact}
    manifest = {"schema_version": 1, "run_id": mode, "started_at": started_at, "finished_at": now(),
                "pass_threshold": 1.0, "expected_cases": ["case-1"], "cases": [record]}
    process = {"mode": mode, "interpreter": str(Path(interpreter).name), "exit_code": returncode,
               "timed_out": timed_out, "output_exists": output.exists(), "output_parse_error": parse_error, "wall_seconds": duration}
    return manifest, process


def evaluate(directory, manifest, expected_integrity, category, process=None):
    path = directory / "manifest.json"
    write_json(path, manifest)
    report = audit_manifest(load_manifest(path), directory)
    write_json(directory / "audit.json", report)
    (directory / "audit.html").write_text(render_html(report), encoding="utf-8")
    if process:
        write_json(directory / "process.json", process)
    entry = {"scenario": manifest["run_id"], "category": category, "expected_integrity": expected_integrity,
             "actual_integrity": report["integrity_ok"], "check_passed": report["integrity_ok"] == expected_integrity,
             "semantic_passed": report["summary"]["semantic_passed"], "success_rate": report["summary"]["success_rate"],
             "issue_codes": sorted({issue["code"] for issue in report["issues"]}),
             "report": f"{directory.name}/audit.html"}
    if process:
        entry["process"] = process
    return entry


def variant(root: Path, name, mutation, expected=False):
    directory = root / name
    manifest, process = execute(directory, "normal", sys.executable)
    manifest["run_id"] = name
    mutation(manifest, directory)
    return evaluate(directory, manifest, expected, "receipt-mutation", process)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--klayout-python", type=Path)
    args = parser.parse_args()
    root = args.output_dir.resolve()
    if root.exists():
        raise SystemExit("Use a new output directory to preserve earlier run evidence")
    root.mkdir(parents=True)
    entries = []
    for mode in ["normal", "wrong-answer", "crash-before", "crash-after", "timeout", "invalid-json", "nan-score"]:
        directory = root / mode
        manifest, process = execute(directory, mode, sys.executable)
        entries.append(evaluate(directory, manifest, mode in ["normal", "wrong-answer"], "real-subprocess", process))
    entries.append(variant(root, "missing-case", lambda m,d: m["expected_cases"].append("case-2")))
    entries.append(variant(root, "duplicate-case", lambda m,d: m["cases"].append(copy.deepcopy(m["cases"][0]))))
    entries.append(variant(root, "extra-case", lambda m,d: m["cases"][0].update(id="unrequested")))
    entries.append(variant(root, "empty-denominator", lambda m,d: m.update(expected_cases=[])))
    entries.append(variant(root, "score-out-of-range", lambda m,d: m["cases"][0].update(score=1.01)))
    entries.append(variant(root, "pass-score-mismatch", lambda m,d: m["cases"][0].update(score=0.0,passed=True)))
    entries.append(variant(root, "artifact-tampered", lambda m,d: (d / "prediction.json").write_text('{"altered":true}',encoding="utf-8")))
    entries.append(variant(root, "artifact-deleted", lambda m,d: (d / "prediction.json").unlink()))
    entries.append(variant(root, "stale-artifact", lambda m,d: (os.utime(d / "prediction.json", (1,1)),m["cases"][0]["artifact"].update(created_at="1970-01-01T00:00:01+00:00"))))
    entries.append(variant(root, "artifact-traversal", lambda m,d: m["cases"][0]["artifact"].update(path="../outside.json")))
    entries.append(variant(root, "absolute-artifact", lambda m,d: m["cases"][0]["artifact"].update(path=str((d / "prediction.json").resolve()))))
    native = None
    if args.klayout_python:
        native = {"enabled":True,"package":"KLayout","interpreter":args.klayout_python.name}
        for mode in ["native-region", "native-crash"]:
            directory = root / mode
            manifest, process = execute(directory, mode, str(args.klayout_python))
            entry = evaluate(directory, manifest, mode == "native-region", "native-klayout", process)
            if mode == "native-region":
                payload = json.loads((directory / "prediction.json").read_text(encoding="utf-8"))
                entry["native_area"] = payload["detail"]["intersection_area"]
                entry["check_passed"] = entry["check_passed"] and entry["native_area"] == 5000
            entries.append(entry)
    # In this deliberately flawed control, a missing output is read as an empty
    # prediction; empty gold and empty prediction produce a nominal perfect score.
    # This demonstrates the risk without making claims about any paper's results.
    baseline = {"gold_predictions":[],"missing_output_fallback":[],"naive_score":1.0,
                "process_exit_code":7,"audit_accepts_as_valid_pass":False}
    summary = {"experiment":"EvalReceipt execution fault matrix", "generated_at":now(),
               "scenario_count":len(entries),"checks_passed":sum(x["check_passed"] for x in entries),
               "control_count":sum(x["expected_integrity"] for x in entries),
               "integrity_fault_count":sum(not x["expected_integrity"] for x in entries),
               "faults_detected":sum(not x["expected_integrity"] and not x["actual_integrity"] for x in entries),
               "all_expected_checks_passed":all(x["check_passed"] for x in entries),
               "native":native,"false_success_control":baseline,"scenarios":entries}
    write_json(root / "summary.json", summary)
    rows="".join(f'<tr><td>{x["scenario"]}</td><td>{x["expected_integrity"]}</td><td>{x["actual_integrity"]}</td><td>{x["success_rate"]}</td><td>{", ".join(x["issue_codes"])}</td></tr>' for x in entries)
    (root / "summary.html").write_text(f'<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>EvalReceipt execution experiment</title><style>body{{max-width:1080px;margin:40px auto;padding:0 24px;font:14px/1.8 system-ui}}table{{border-collapse:collapse;width:100%}}td,th{{padding:10px;text-align:left;border-bottom:1px solid #dce3e8;overflow-wrap:anywhere}}.wrap{{overflow-x:auto}}</style><h1>EvalReceipt execution experiment</h1><p>{len(entries)} scenarios. {summary["checks_passed"]} expected checks passed. {summary["faults_detected"]} injected integrity faults detected.</p><p>The wrong-answer control is a complete execution with semantic score zero. {"Native KLayout control computes an intersection area of 5,000 square database units." if native else "Add --klayout-python to exercise two optional native-library scenarios."}</p><div class="wrap"><table><tr><th>Scenario</th><th>Expected integrity</th><th>Observed integrity</th><th>Success rate</th><th>Findings</th></tr>{rows}</table></div></html>',encoding="utf-8")
    print(json.dumps({k:v for k,v in summary.items() if k not in ["scenarios","native"]},ensure_ascii=False,indent=2))
    return 0 if summary["all_expected_checks_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
