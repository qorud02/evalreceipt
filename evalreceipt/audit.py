"""Strict receipt parsing and independent case-evidence checks."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any


class InputError(ValueError):
    """The input does not satisfy the manifest contract."""


TOP_FIELDS = {"schema_version", "run_id", "started_at", "finished_at", "pass_threshold", "expected_cases", "cases"}
CASE_FIELDS = {"id", "status", "exit_code", "timed_out", "score", "passed", "artifact"}
ARTIFACT_FIELDS = {"path", "sha256", "created_at"}


def _object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise InputError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _constant(value: str) -> Any:
    raise InputError(f"Nonfinite JSON number: {value}")


def _keys(value: Any, expected: set[str], location: str) -> None:
    if type(value) is not dict:
        raise InputError(f"{location} must be an object")
    missing = sorted(expected - value.keys())
    unknown = sorted(value.keys() - expected)
    if missing or unknown:
        raise InputError(f"{location}: missing fields {missing}; unknown fields {unknown}")


def _text(value: Any, location: str) -> None:
    if type(value) is not str or not value.strip():
        raise InputError(f"{location} must be a nonempty string")
    if any(0xD800 <= ord(char) <= 0xDFFF for char in value):
        raise InputError(f"{location} contains an unpaired Unicode surrogate")


def timestamp(value: Any, location: str) -> datetime:
    _text(value, location)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise InputError(f"{location} must be an ISO 8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise InputError(f"{location} must include a UTC offset")
    try:
        return parsed.astimezone(timezone.utc)
    except (OverflowError, ValueError) as exc:
        raise InputError(f"{location} cannot be represented in UTC") from exc


def _number(value: Any, location: str, nullable: bool = False) -> None:
    if nullable and value is None:
        return
    if type(value) not in (int, float):
        raise InputError(f"{location} must be a finite number")
    try:
        finite = math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite:
        raise InputError(f"{location} must be a finite number")


def validate_manifest(manifest: Any) -> dict[str, Any]:
    """Validate structure only; evidence inconsistencies are audit findings."""
    _keys(manifest, TOP_FIELDS, "manifest")
    if type(manifest["schema_version"]) is not int or manifest["schema_version"] != 1:
        raise InputError("schema_version must be integer 1")
    _text(manifest["run_id"], "run_id")
    start = timestamp(manifest["started_at"], "started_at")
    finish = timestamp(manifest["finished_at"], "finished_at")
    if finish < start:
        raise InputError("finished_at must be at or after started_at")
    _number(manifest["pass_threshold"], "pass_threshold")
    if not 0 <= manifest["pass_threshold"] <= 1:
        raise InputError("pass_threshold must be between 0 and 1")
    for field in ("expected_cases", "cases"):
        if type(manifest[field]) is not list:
            raise InputError(f"{field} must be an array")
    for index, case_id in enumerate(manifest["expected_cases"]):
        _text(case_id, f"expected_cases[{index}]")
    if len(set(manifest["expected_cases"])) != len(manifest["expected_cases"]):
        raise InputError("expected_cases must contain unique IDs")
    for index, record in enumerate(manifest["cases"]):
        loc = f"cases[{index}]"
        _keys(record, CASE_FIELDS, loc)
        _text(record["id"], f"{loc}.id")
        if record["status"] not in ("completed", "failed", "timeout") or type(record["status"]) is not str:
            raise InputError(f"{loc}.status must be completed, failed, or timeout")
        code = record["exit_code"]
        if code is not None and type(code) is not int:
            raise InputError(f"{loc}.exit_code must be an integer or null")
        if type(record["timed_out"]) is not bool:
            raise InputError(f"{loc}.timed_out must be boolean")
        _number(record["score"], f"{loc}.score", nullable=True)
        if record["passed"] is not None and type(record["passed"]) is not bool:
            raise InputError(f"{loc}.passed must be boolean or null")
        artifact = record["artifact"]
        if artifact is not None:
            _keys(artifact, ARTIFACT_FIELDS, f"{loc}.artifact")
            _text(artifact["path"], f"{loc}.artifact.path")
            if type(artifact["sha256"]) is not str or re.fullmatch(r"[0-9a-fA-F]{64}", artifact["sha256"]) is None:
                raise InputError(f"{loc}.artifact.sha256 must be 64 hexadecimal characters")
            timestamp(artifact["created_at"], f"{loc}.artifact.created_at")
    return manifest


def load_manifest(path: str | Path) -> dict[str, Any]:
    try:
        with Path(path).open("r", encoding="utf-8") as stream:
            raw = json.load(stream, object_pairs_hook=_object_pairs, parse_constant=_constant)
    except (OSError, UnicodeError, ValueError, RecursionError) as exc:
        raise InputError(f"Cannot read manifest: {exc}") from exc
    return validate_manifest(raw)


def _safe_artifact(base: Path, raw_path: str) -> Path:
    """Require a portable relative path and reject links before dereferencing."""
    posix = PurePosixPath(raw_path)
    windows = PureWindowsPath(raw_path)
    if (posix.is_absolute() or windows.drive or windows.is_absolute()
            or "\\" in raw_path or ".." in posix.parts or ":" in raw_path
            or "\x00" in raw_path or raw_path in (".", "")):
        raise ValueError("Artifact path must be a relative POSIX path without parent traversal")
    candidate = base.joinpath(*posix.parts)
    cursor = base
    for part in posix.parts:
        cursor = cursor / part
        if cursor.is_symlink() or (hasattr(cursor, "is_junction") and cursor.is_junction()):
            raise ValueError("Artifact path contains a symbolic link or junction")
    resolved = candidate.resolve()
    if not resolved.is_relative_to(base):
        raise ValueError("Artifact resolves outside the manifest directory")
    return candidate


def audit_manifest(manifest: dict[str, Any], base_dir: str | Path) -> dict[str, Any]:
    """Audit a manifest against files under its directory. Never runs an artifact."""
    validate_manifest(manifest)
    try:
        base = Path(base_dir).resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise InputError(f"Cannot resolve manifest directory: {exc}") from exc
    if not base.is_dir():
        raise InputError("Manifest directory must be a directory")
    start = timestamp(manifest["started_at"], "started_at")
    finish = timestamp(manifest["finished_at"], "finished_at")
    expected = manifest["expected_cases"]
    expected_set = set(expected)
    counts = Counter(record["id"] for record in manifest["cases"])
    issues: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []

    def issue(code: str, message: str, case_id: str | None = None, index: int | None = None) -> None:
        item: dict[str, Any] = {"code": code, "message": message}
        if case_id is not None:
            item["case_id"] = case_id
        if index is not None:
            item["record_index"] = index
        issues.append(item)

    if not expected:
        issue("empty_expected_cases", "Expected case denominator is empty")
    for case_id in expected:
        if counts[case_id] == 0:
            issue("missing_case", "Expected case has no record", case_id)
    for case_id, count in counts.items():
        if count > 1:
            issue("duplicate_case", f"Case has {count} records", case_id)
        if case_id not in expected_set:
            issue("extra_case", "Case is absent from expected_cases", case_id)

    for index, record in enumerate(manifest["cases"]):
        case_id = record["id"]
        local_codes: list[str] = []

        def local(code: str, message: str) -> None:
            local_codes.append(code)
            issue(code, message, case_id, index)

        if counts[case_id] > 1:
            local_codes.append("duplicate_case")
        if case_id not in expected_set:
            local_codes.append("extra_case")
        if record["status"] != "completed":
            local("incomplete_execution", f"Case status is {record['status']}")
        if record["exit_code"] != 0:
            local("process_failure", "Case did not record exit code 0")
        if record["timed_out"] or record["status"] == "timeout":
            local("timeout", "Case timed out")
        score = record["score"]
        if score is None:
            local("missing_score", "Case has no score")
        elif not 0 <= score <= 1:
            local("score_out_of_range", "Score must be between 0 and 1")
        if record["passed"] is None:
            local("missing_passed", "Case has no semantic pass result")
        elif score is not None and 0 <= score <= 1 and record["passed"] != (score >= manifest["pass_threshold"]):
            local("pass_score_mismatch", "Semantic pass flag disagrees with score and pass_threshold")
        artifact = record["artifact"]
        artifact_checked = False
        if artifact is None:
            local("missing_artifact", "Case has no output artifact receipt")
        else:
            artifact_date = timestamp(artifact["created_at"], "artifact.created_at")
            if artifact_date < start or artifact_date > finish:
                local("stale_artifact_timestamp", "Artifact timestamp is outside the run interval")
            try:
                path = _safe_artifact(base, artifact["path"])
                if not path.is_file():
                    local("missing_artifact", "Artifact is absent or is not a regular file")
                else:
                    # Hash and timestamps are checked through one open descriptor.
                    # This reduces races, while callers should use immutable run directories.
                    import os
                    import stat
                    with path.open("rb") as stream:
                        initial = os.fstat(stream.fileno())
                        if not stat.S_ISREG(initial.st_mode):
                            local("invalid_artifact_type", "Artifact is not a regular file")
                        elif initial.st_mtime < start.timestamp() or initial.st_mtime > finish.timestamp():
                            local("stale_artifact_mtime", "Artifact modification time is outside the run interval")
                        digest = hashlib.file_digest(stream, "sha256").hexdigest()
                        final = os.fstat(stream.fileno())
                    artifact_checked = True
                    if (initial.st_mtime_ns, initial.st_size) != (final.st_mtime_ns, final.st_size):
                        local("artifact_changed", "Artifact changed during hashing")
                    if digest != artifact["sha256"].lower():
                        local("hash_mismatch", "Artifact SHA-256 differs from the receipt")
            except ValueError as exc:
                local("unsafe_artifact_path", str(exc))
            except (OSError, RuntimeError) as exc:
                local("artifact_read_error", f"Cannot inspect artifact: {exc}")
        valid = not local_codes
        results.append({"id": case_id, "record_index": index, "valid": valid,
                        "status": record["status"], "score": score, "passed": record["passed"],
                        "artifact_checked": artifact_checked, "issue_codes": local_codes})

    valid_cases = [record for record in results if record["valid"]]
    passed = sum(record["passed"] is True for record in valid_cases)
    total = len(expected)
    report = {
        "report_version": 1, "tool": "EvalReceipt", "run_id": manifest["run_id"],
        "integrity_ok": not issues, "started_at": manifest["started_at"], "finished_at": manifest["finished_at"],
        "summary": {"expected": total, "records": len(results), "valid_completed": len(valid_cases),
                    "semantic_passed": passed, "semantic_failed": len(valid_cases) - passed,
                    "completion_rate": len(valid_cases) / total if total else None,
                    "success_rate": passed / total if total else None,
                    "mean_valid_score": sum(record["score"] for record in valid_cases) / len(valid_cases) if valid_cases else None,
                    "issue_count": len(issues)},
        "cases": results, "issues": issues,
    }
    return report
