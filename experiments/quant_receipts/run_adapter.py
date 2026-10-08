"""Re-run stored TinyQuant models and audit their execution receipts.

Only the local TinyQuant ``run`` command is executed; training is never called.
Source results, models, dataset, and split files are read-only inputs.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"

OWN_ROOT = Path(__file__).resolve().parent
PROJECT = OWN_ROOT.parents[1]
TINYQUANT = PROJECT.parent / "tinyquant-lab"
sys.path.insert(0, str(PROJECT))
np = None
tq = None
from evalreceipt import audit_manifest
from evalreceipt.render import render_html


def stamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def case_id(record: dict) -> str:
    seed, method, bits = record["seed"], record["method"], record["bits"]
    if type(seed) is not int or type(bits) is not int or not re.fullmatch(r"[A-Za-z0-9_-]+", method):
        raise ValueError("Source model identity has an invalid seed, method, or bits field")
    return f"seed-{seed}__{method}__{bits}bit"


def audit_save(folder: Path, manifest: dict) -> dict:
    write_json(folder / "manifest.json", manifest)
    report = audit_manifest(manifest, folder)
    write_json(folder / "audit.json", report)
    (folder / "audit.html").write_text(render_html(report), encoding="utf-8")
    return report


def source_models(results: Path, payload: dict, expected_count: int) -> list[tuple[dict, Path]]:
    records = payload["records"]
    if len(records) != expected_count:
        raise ValueError(f"Final results require {expected_count} models; found {len(records)}")
    identities = [case_id(record) for record in records]
    if len(set(identities)) != len(identities):
        raise ValueError("Source model identities are duplicated")
    if any("randomized round-robin" not in record.get("timing", {}).get("scheme", "") for record in records):
        raise ValueError("Wait for the final interleaved rebenchmark results before running this adapter")
    result = []
    root = results.parent.resolve()
    for record in records:
        filename = record["model_path"]
        if type(filename) is not str or Path(filename).name != filename or filename in (".", ".."):
            raise ValueError("Model paths must be filenames in their seed directory")
        source = root / f"seed-{record['seed']}" / filename
        if source.is_symlink() or not source.resolve().is_relative_to(root) or not source.is_file():
            raise ValueError(f"Missing or unsafe source model: {filename}")
        result.append((record, source))
    return result


def clean_run(folder: Path, results: Path, payload: dict, models: list[tuple[dict, Path]], timeout: float):
    folder.mkdir(parents=True, exist_ok=False)
    started = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    source_results_sha = sha(results)
    source_code_sha = sha(TINYQUANT / "tinyquant.py")
    source_fingerprints = {str(results): source_results_sha, str(TINYQUANT / "tinyquant.py"): source_code_sha}
    for dataset_file in (TINYQUANT / "dataset" / "digits.csv.gz", TINYQUANT / "dataset" / "provenance.json"):
        if dataset_file.exists():
            source_fingerprints[str(dataset_file)] = sha(dataset_file)
    x, y, data_provenance = tq.load_data()
    prepared = {}
    for seed in sorted({record["seed"] for record, _ in models}):
        seed_dir = results.parent / f"seed-{seed}"
        split_path = seed_dir / "split.npz"
        source_fingerprints[str(split_path)] = sha(split_path)
        with np.load(split_path, allow_pickle=False) as split:
            train, calibration, test = (split[key].copy() for key in ("train", "calibration", "test"))
        tq.assert_disjoint(train, calibration, test, len(y))
        metadata_path = seed_dir / "metadata.json"
        source_fingerprints[str(metadata_path)] = sha(metadata_path)
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if metadata["data"]["normalized_data_sha256"] != data_provenance["normalized_data_sha256"]:
            raise ValueError("Saved split dataset fingerprint differs from the inference dataset")
        split_hash = hashlib.sha256(train.tobytes() + calibration.tobytes() + test.tobytes()).hexdigest()
        if split_hash != metadata["split_sha256"]:
            raise ValueError("Saved split identities differ from the training record")
        inputs = folder / "inputs" / f"seed-{seed}-features.npy"
        labels = folder / "inputs" / f"seed-{seed}-labels.npy"
        inputs.parent.mkdir(exist_ok=True)
        np.save(inputs, x[test], allow_pickle=False)
        np.save(labels, y[test], allow_pickle=False)
        prepared[seed] = {"input": inputs, "labels": labels, "test_labels": y[test],
                          "test_samples": len(test), "source_split": split_path.relative_to(results.parent).as_posix(),
                          "source_split_sha256": sha(split_path), "input_sha256": sha(inputs), "labels_sha256": sha(labels)}

    cases, traces = [], []
    for record, original in models:
        identity = case_id(record)
        model = folder / "models" / (identity + ".npz")
        predictions = folder / "predictions" / (identity + ".npy")
        model.parent.mkdir(exist_ok=True)
        predictions.parent.mkdir(exist_ok=True)
        original_hash = sha(original)
        source_fingerprints[str(original)] = original_hash
        copy_started = stamp()
        shutil.copyfile(original, model)
        copy_finished = stamp()
        copied_hash = sha(model)
        if copied_hash != original_hash:
            raise ValueError("Copied model bytes differ from the source")
        prepared_seed = prepared[record["seed"]]
        command = [sys.executable, str(TINYQUANT / "tinyquant.py"), "run", "--model", str(model),
                   "--input", str(prepared_seed["input"]), "--output", str(predictions)]
        inference_started = stamp()
        timer = time.perf_counter()
        timed_out, exit_code = False, None
        stdout, stderr = "", ""
        try:
            process = subprocess.run(command, capture_output=True, text=True, timeout=timeout,
                                     cwd=folder, env=os.environ.copy())
            exit_code, stdout, stderr = process.returncode, process.stdout, process.stderr
        except subprocess.TimeoutExpired:
            timed_out = True
        inference_finished = stamp()
        elapsed = time.perf_counter() - timer
        score, passed, prediction_sha, prediction_error = None, None, None, None
        if exit_code == 0 and not timed_out:
            try:
                values = np.load(predictions, allow_pickle=False)
                expected_shape = (prepared_seed["test_samples"], 10)
                if values.shape != expected_shape or not np.all(np.isfinite(values)):
                    raise ValueError("Prediction matrix shape or finite-value check failed")
                correct = int(np.sum(np.argmax(values, axis=1) == prepared_seed["test_labels"]))
                score = correct / prepared_seed["test_samples"]
                passed = score >= 0.9
                prediction_sha = sha(predictions)
            except (OSError, ValueError) as exc:
                prediction_error = str(exc)
        status = "timeout" if timed_out else "completed" if exit_code == 0 and score is not None else "failed"
        cases.append({"id": identity, "status": status, "exit_code": exit_code,
                      "timed_out": timed_out, "score": score, "passed": passed,
                      "artifact": {"path": model.relative_to(folder).as_posix(), "sha256": copied_hash, "created_at": copy_finished}})
        source_score = float(record["accuracy"])
        match = score is not None and abs(score - source_score) <= 1e-12
        traces.append({"id": identity, "seed": record["seed"], "method": record["method"], "bits": record["bits"],
                       "source_model": original.relative_to(results.parent).as_posix(), "source_model_sha256": original_hash,
                       "copied_model": model.relative_to(folder).as_posix(), "copied_model_sha256": copied_hash,
                       "copy_started_at": copy_started, "copy_finished_at": copy_finished,
                       "model_mtime": datetime.fromtimestamp(model.stat().st_mtime, timezone.utc).isoformat(),
                       "inference_started_at": inference_started, "inference_finished_at": inference_finished,
                       "process_elapsed_seconds": elapsed, "exit_code": exit_code, "timed_out": timed_out,
                       "stdout": stdout[-1000:], "stderr": stderr[-1000:], "prediction_error": prediction_error,
                       "test_samples": prepared_seed["test_samples"], "recomputed_accuracy": score,
                       "supplied_accuracy": source_score, "accuracy_matches_supplied": match,
                       "prediction_sha256": prediction_sha, "command": [Path(sys.executable).name, "tinyquant.py", "run", "--model", model.relative_to(folder).as_posix(), "--input", prepared_seed["input"].relative_to(folder).as_posix(), "--output", predictions.relative_to(folder).as_posix()]})
        print(json.dumps({"id": identity, "recomputed_accuracy": score, "matches": match}), flush=True)
    finished = stamp()
    manifest = {"schema_version": 1, "run_id": "tinyquant-21-independent-inference",
                "started_at": started, "finished_at": finished, "pass_threshold": 0.9,
                "expected_cases": [case_id(record) for record, _ in models], "cases": cases}
    audit = audit_save(folder, manifest)
    source_unchanged = all(sha(Path(path)) == digest for path, digest in source_fingerprints.items())
    public_hashes = {(
        "results/" + Path(path).relative_to(results.parent).as_posix()
        if Path(path).resolve().is_relative_to(results.parent.resolve())
        else "tinyquant/" + Path(path).relative_to(TINYQUANT).as_posix()
    ): digest for path, digest in source_fingerprints.items()}
    provenance = {"source_results": results.name, "source_results_sha256": source_results_sha,
                  "tinyquant_source_sha256": source_code_sha, "dataset": data_provenance,
                  "original_inputs_unchanged": source_unchanged,
                  "source_input_hashes": public_hashes,
                  "training_executed": False, "subprocess_count": len(traces), "pass_threshold": 0.9,
                  "artifact_role": "Copied model bytes used by the recorded inference process",
                  "timing_scope": "Process wall time including launch, imports, model loading and output file IO; not an inference benchmark",
                  "splits": [{key: value for key, value in row.items() if key not in ("input", "labels", "test_labels")} for row in prepared.values()],
                  "traces": traces}
    write_json(folder / "provenance.json", provenance)
    return manifest, audit, provenance


def scenario(folder: Path, clean: Path, clean_manifest: dict, name: str):
    folder.mkdir(parents=True, exist_ok=False)
    manifest = deepcopy(clean_manifest)
    manifest["run_id"] = f"tinyquant-{name}"
    manifest["started_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    for case in manifest["cases"]:
        relative = Path(case["artifact"]["path"])
        destination = folder / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(clean / relative, destination)
        case["artifact"]["created_at"] = stamp()
    target_id = manifest["cases"][0]["id"]
    if name == "tampered-model":
        target = folder / manifest["cases"][0]["artifact"]["path"]
        payload = bytearray(target.read_bytes())
        payload[-1] ^= 1
        target.write_bytes(payload)
    elif name == "missing-record":
        manifest["cases"].pop(0)
    else:
        raise ValueError("Unknown controlled scenario")
    manifest["finished_at"] = stamp()
    report = audit_save(folder, manifest)
    write_json(folder / "scenario.json", {"name": name, "changed_case": target_id,
                                           "source_clean_manifest_sha256": sha(clean / "manifest.json"),
                                           "expected_issue": "hash_mismatch" if name == "tampered-model" else "missing_case"})
    return report


def run(results: Path, output: Path, expected_models: int, timeout: float) -> dict:
    if not output.resolve().is_relative_to(OWN_ROOT.resolve()) or output.resolve() == OWN_ROOT.resolve():
        raise ValueError("Output must be a new child directory under quant_receipts")
    if output.exists():
        raise ValueError("Choose a new output directory; existing experiment evidence is preserved")
    payload = json.loads(results.read_text(encoding="utf-8"))
    models = source_models(results, payload, expected_models)
    output.mkdir(parents=True, exist_ok=False)
    clean = output / "clean"
    manifest, clean_report, provenance = clean_run(clean, results, payload, models, timeout)
    reports = {"clean": clean_report}
    for name in ("tampered-model", "missing-record"):
        reports[name] = scenario(output / name, clean, manifest, name)
    rows = []
    for name, report in reports.items():
        expected_integrity = name == "clean"
        codes = sorted({issue["code"] for issue in report["issues"]})
        required = "hash_mismatch" if name == "tampered-model" else "missing_case" if name == "missing-record" else None
        checks_passed = report["integrity_ok"] == expected_integrity and (required is None or required in codes)
        if name == "clean":
            checks_passed = checks_passed and report["summary"]["valid_completed"] == expected_models
        rows.append({"name": name, "expected_integrity": expected_integrity,
                     "actual_integrity": report["integrity_ok"], "issue_codes": codes, "checks_passed": checks_passed,
                     "expected": report["summary"]["expected"], "valid_completed": report["summary"]["valid_completed"],
                     "semantic_passed": report["summary"]["semantic_passed"], "semantic_failed": report["summary"]["semantic_failed"],
                     "manifest": (output / name / "manifest.json").relative_to(OWN_ROOT).as_posix(),
                     "audit_json": (output / name / "audit.json").relative_to(OWN_ROOT).as_posix(),
                     "audit_html": (output / name / "audit.html").relative_to(OWN_ROOT).as_posix()})
    accuracy_matches = sum(trace["accuracy_matches_supplied"] for trace in provenance["traces"])
    summary = {"tool": "TinyQuant → EvalReceipt adapter", "created_at": stamp(),
               "model_count": expected_models, "clean_integrity_ok": clean_report["integrity_ok"],
               "subprocess_count": provenance["subprocess_count"], "accuracy_matches_supplied_count": accuracy_matches,
               "accuracy_threshold": 0.9, "training_executed": False,
               "source_inputs_unchanged": provenance["original_inputs_unchanged"],
               "scenarios": rows, "output_directory": output.relative_to(OWN_ROOT).as_posix(),
               "source_results_sha256": provenance["source_results_sha256"],
               "all_expected_checks_passed": all(row["checks_passed"] for row in rows)
                    and accuracy_matches == expected_models and provenance["original_inputs_unchanged"]}
    write_json(output / "summary.json", summary)
    write_json(OWN_ROOT / "summary.json", summary)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run stored TinyQuant inference and audit copied-model receipts")
    parser.add_argument("--tinyquant-root", type=Path, default=TINYQUANT, help="TinyQuant checkout containing tinyquant.py and dataset/")
    parser.add_argument("--results", type=Path, help="Saved results.json, including its seed directories")
    parser.add_argument("--out", type=Path, default=OWN_ROOT / "runs" / "reproduction")
    parser.add_argument("--expected-models", type=int, default=21)
    parser.add_argument("--timeout", type=float, default=90)
    args = parser.parse_args()
    TINYQUANT = args.tinyquant_root.resolve()
    if not (TINYQUANT / "tinyquant.py").is_file():
        parser.error("--tinyquant-root must contain tinyquant.py")
    sys.path.insert(0, str(TINYQUANT))
    import numpy as np
    import tinyquant as tq
    results = args.results or TINYQUANT / "results" / "results.json"
    summary = run(results.resolve(), args.out.resolve(), args.expected_models, args.timeout)
    print(json.dumps(summary, indent=2))
    raise SystemExit(0 if summary["all_expected_checks_passed"] else 1)
