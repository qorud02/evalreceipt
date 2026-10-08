"""Generate local fault-injection inputs with reproducible hashes and timestamps."""

from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path


def build(destination: Path) -> list[Path]:
    destination.mkdir(parents=True, exist_ok=True)
    start = datetime.now(timezone.utc) - timedelta(seconds=2)
    artifact_dir = destination / "artifacts"
    artifact_dir.mkdir(exist_ok=True)
    content = b'{"prediction": "ok"}\n'
    artifact = artifact_dir / "prediction.json"
    artifact.write_bytes(content)
    empty = artifact_dir / "empty.json"
    empty.write_bytes(b"")
    created = datetime.now(timezone.utc).isoformat()
    manifest = {
        "schema_version": 1, "run_id": "clean-control", "started_at": start.isoformat(),
        "finished_at": (datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat(),
        "pass_threshold": 1.0, "expected_cases": ["case-a", "case-b"],
        "cases": [{"id": case_id, "status": "completed", "exit_code": 0, "timed_out": False,
                   "score": score, "passed": score == 1,
                   "artifact": {"path": "artifacts/prediction.json", "sha256": hashlib.sha256(content).hexdigest(), "created_at": created}}
                  for case_id, score in [("case-a", 1.0), ("case-b", 1.0)]],
    }
    fixtures = {"clean": manifest}

    def variant(name: str):
        copy = deepcopy(manifest)
        copy["run_id"] = name
        fixtures[name] = copy
        return copy

    semantic = variant("semantic-failure")
    semantic["cases"][1].update(score=0.0, passed=False)
    failed = variant("failed-empty-output")
    failed["cases"][0].update(status="failed", exit_code=1, score=1.0, passed=True)
    failed["cases"][0]["artifact"].update(path="artifacts/empty.json", sha256=hashlib.sha256(b"").hexdigest())
    timeout = variant("timeout")
    timeout["cases"][0].update(status="timeout", timed_out=True, exit_code=None, artifact=None, score=None, passed=None)
    variant("missing-case")["cases"].pop()
    duplicate = variant("duplicate-case")
    duplicate["cases"].append(deepcopy(duplicate["cases"][0]))
    extra = variant("extra-case")
    extra["cases"].append({**deepcopy(extra["cases"][0]), "id": "unexpected"})
    variant("score-out-of-range")["cases"][0]["score"] = 2.0
    variant("hash-mismatch")["cases"][0]["artifact"]["sha256"] = "0" * 64
    variant("missing-artifact")["cases"][0]["artifact"]["path"] = "artifacts/absent.json"
    variant("stale-artifact")["cases"][0]["artifact"]["created_at"] = (start - timedelta(days=1)).isoformat()
    empty_denominator = variant("empty-denominator")
    empty_denominator.update(expected_cases=[], cases=[])
    variant("path-traversal")["cases"][0]["artifact"]["path"] = "../outside.json"
    paths = []
    for name, payload in fixtures.items():
        path = destination / f"{name}.json"
        path.write_text(json.dumps(payload, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        paths.append(path)
    invalid = deepcopy(manifest)
    invalid["run_id"] = "nonfinite-input"
    text = json.dumps(invalid).replace('"score": 1.0', '"score": NaN', 1)
    path = destination / "nonfinite-input.json"
    path.write_text(text + "\n", encoding="utf-8")
    paths.append(path)
    return paths


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=Path("fixtures"))
    args = parser.parse_args()
    for path in build(args.out):
        print(path)
