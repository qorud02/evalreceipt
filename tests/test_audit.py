from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from evalreceipt.audit import InputError, audit_manifest, load_manifest
from evalreceipt.render import render_html


class ReceiptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        now = datetime.now(timezone.utc)
        (self.base / "prediction.json").write_bytes(b"prediction")
        self.manifest = {
            "schema_version": 1, "run_id": "control", "started_at": (now - timedelta(seconds=2)).isoformat(),
            "finished_at": (now + timedelta(seconds=2)).isoformat(), "pass_threshold": 1.0,
            "expected_cases": ["a", "b"], "cases": [],
        }
        for case_id in ("a", "b"):
            self.manifest["cases"].append({"id": case_id, "status": "completed", "exit_code": 0,
                "timed_out": False, "score": 1.0, "passed": True,
                "artifact": {"path": "prediction.json", "sha256": hashlib.sha256(b"prediction").hexdigest(), "created_at": now.isoformat()}})

    def audit(self):
        return audit_manifest(self.manifest, self.base)

    def codes(self):
        return {item["code"] for item in self.audit()["issues"]}

    def cli(self, *args, text=None):
        path = self.base / "manifest.json"
        path.write_text(text if text is not None else json.dumps(self.manifest), encoding="utf-8")
        result = subprocess.run([sys.executable, "-m", "evalreceipt", str(path), *map(str, args)],
                                capture_output=True, text=True, cwd=Path(__file__).resolve().parents[1], timeout=30)
        return result, json.loads(result.stdout)

    def test_clean_control(self):
        report = self.audit()
        self.assertTrue(report["integrity_ok"])
        self.assertEqual(report["summary"]["valid_completed"], 2)
        self.assertEqual(report["summary"]["success_rate"], 1)

    def test_semantic_failure_is_valid_evidence(self):
        self.manifest["cases"][1].update(score=0, passed=False)
        report = self.audit()
        self.assertTrue(report["integrity_ok"])
        self.assertEqual(report["summary"]["semantic_failed"], 1)
        self.assertEqual(report["summary"]["success_rate"], 0.5)
        self.assertEqual(self.cli()[0].returncode, 0)

    def test_failed_empty_output_cannot_pass(self):
        (self.base / "empty.json").write_bytes(b"")
        case = self.manifest["cases"][0]
        case.update(status="failed", exit_code=1)
        case["artifact"].update(path="empty.json", sha256=hashlib.sha256(b"").hexdigest())
        report = self.audit()
        self.assertFalse(report["integrity_ok"])
        self.assertEqual(report["summary"]["semantic_passed"], 1)
        self.assertEqual(report["summary"]["completion_rate"], 0.5)

    def test_nonzero_exit_despite_completed_flag(self):
        self.manifest["cases"][0]["exit_code"] = 7
        self.assertIn("process_failure", self.codes())

    def test_missing_exit_code(self):
        self.manifest["cases"][0]["exit_code"] = None
        self.assertIn("process_failure", self.codes())

    def test_timeout_despite_zero_exit(self):
        self.manifest["cases"][0]["timed_out"] = True
        self.assertIn("timeout", self.codes())

    def test_timeout_status_despite_false_flag(self):
        self.manifest["cases"][0]["status"] = "timeout"
        self.assertIn("timeout", self.codes())

    def test_missing_case_keeps_expected_denominator(self):
        self.manifest["cases"].pop()
        report = self.audit()
        self.assertEqual(report["summary"]["success_rate"], 0.5)
        self.assertIn("missing_case", {item["code"] for item in report["issues"]})

    def test_duplicate_records_do_not_inflate_score(self):
        self.manifest["cases"].append(deepcopy(self.manifest["cases"][0]))
        report = self.audit()
        self.assertEqual(report["summary"]["valid_completed"], 1)
        self.assertEqual(report["summary"]["success_rate"], 0.5)
        self.assertIn("duplicate_case", {item["code"] for item in report["issues"]})

    def test_extra_record_excluded(self):
        self.manifest["cases"].append({**deepcopy(self.manifest["cases"][0]), "id": "extra"})
        report = self.audit()
        self.assertEqual(report["summary"]["semantic_passed"], 2)
        self.assertEqual(report["summary"]["success_rate"], 1)
        self.assertFalse(report["integrity_ok"])

    def test_empty_denominator_is_issue_not_perfect_score(self):
        self.manifest.update(expected_cases=[], cases=[])
        report = self.audit()
        self.assertFalse(report["integrity_ok"])
        self.assertIsNone(report["summary"]["success_rate"])

    def test_out_of_range_scores(self):
        for score in (-0.1, 1.1):
            with self.subTest(score=score):
                self.manifest["cases"][0]["score"] = score
                self.assertIn("score_out_of_range", self.codes())

    def test_missing_score_and_pass_flag(self):
        self.manifest["cases"][0].update(score=None, passed=None)
        self.assertTrue({"missing_score", "missing_passed"}.issubset(self.codes()))

    def test_pass_flag_must_match_threshold(self):
        self.manifest["cases"][0].update(score=0.3, passed=True)
        self.assertIn("pass_score_mismatch", self.codes())

    def test_custom_threshold(self):
        self.manifest["pass_threshold"] = 0.5
        self.manifest["cases"][0].update(score=0.5, passed=True)
        self.assertTrue(self.audit()["integrity_ok"])

    def test_absent_artifact(self):
        self.manifest["cases"][0]["artifact"]["path"] = "missing.json"
        self.assertIn("missing_artifact", self.codes())

    def test_null_artifact(self):
        self.manifest["cases"][0]["artifact"] = None
        self.assertIn("missing_artifact", self.codes())

    def test_hash_mismatch(self):
        self.manifest["cases"][0]["artifact"]["sha256"] = "0" * 64
        self.assertIn("hash_mismatch", self.codes())

    def test_uppercase_sha_is_valid(self):
        self.manifest["cases"][0]["artifact"]["sha256"] = self.manifest["cases"][0]["artifact"]["sha256"].upper()
        self.assertTrue(self.audit()["integrity_ok"])

    def test_stale_declared_timestamp(self):
        self.manifest["cases"][0]["artifact"]["created_at"] = "2000-01-01T00:00:00Z"
        self.assertIn("stale_artifact_timestamp", self.codes())

    def test_stale_actual_mtime(self):
        os.utime(self.base / "prediction.json", (1, 1))
        self.assertIn("stale_artifact_mtime", self.codes())

    def test_future_actual_mtime(self):
        future = datetime.now(timezone.utc).timestamp() + 100
        os.utime(self.base / "prediction.json", (future, future))
        self.assertIn("stale_artifact_mtime", self.codes())

    def test_traversal_and_absolute_paths(self):
        for path in ("../outside", "/etc/passwd", "C:/Windows/file", "C:relative", "\\\\server\\file", "a\\b", "a/../../file", "x:stream", "\x00"):
            with self.subTest(path=path):
                self.manifest["cases"][0]["artifact"]["path"] = path
                self.assertIn("unsafe_artifact_path", self.codes())

    def test_symlink_is_rejected(self):
        link = self.base / "linked.json"
        try:
            link.symlink_to(self.base / "prediction.json")
        except OSError as exc:
            self.skipTest(f"Symlink unavailable: {exc}")
        self.manifest["cases"][0]["artifact"]["path"] = "linked.json"
        self.assertIn("unsafe_artifact_path", self.codes())

    def test_directory_symlink_escape_is_rejected(self):
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        (Path(outside.name) / "prediction.json").write_bytes(b"prediction")
        link = self.base / "linked-dir"
        try:
            link.symlink_to(outside.name, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"Symlink unavailable: {exc}")
        self.manifest["cases"][0]["artifact"]["path"] = "linked-dir/prediction.json"
        self.assertIn("unsafe_artifact_path", self.codes())

    def test_strict_structure(self):
        for mutate in (lambda m: m.update(extra=1), lambda m: m.pop("run_id"),
                       lambda m: m.update(schema_version=True), lambda m: m.update(expected_cases=["a", "a"]),
                       lambda m: m["cases"][0].update(timed_out="false"), lambda m: m["cases"][0].update(score=True),
                       lambda m: m["cases"][0].update(exit_code=False), lambda m: m["cases"][0].update(status="running"),
                       lambda m: m["cases"][0]["artifact"].update(sha256="abc"),
                       lambda m: m.update(started_at="2026-01-01")):
            with self.subTest(mutate=mutate):
                payload = deepcopy(self.manifest)
                mutate(payload)
                with self.assertRaises(InputError):
                    audit_manifest(payload, self.base)

    def test_nonfinite_numbers_rejected(self):
        for number in (float("nan"), float("inf"), -float("inf")):
            with self.subTest(number=number):
                self.manifest["cases"][0]["score"] = number
                with self.assertRaises(InputError):
                    self.audit()

    def test_duplicate_json_keys_rejected(self):
        result, report = self.cli(text='{"schema_version":1,"schema_version":1}')
        self.assertEqual(result.returncode, 2)
        self.assertIn("Duplicate JSON key", report["error"])

    def test_json_nonfinite_and_overflow_rejected(self):
        for token in ("NaN", "Infinity", "-Infinity", "1e999"):
            with self.subTest(token=token):
                text = json.dumps(self.manifest).replace('"score": 1.0', '"score": ' + token, 1)
                self.assertEqual(self.cli(text=text)[0].returncode, 2)

    def test_unpaired_unicode_surrogate_rejected(self):
        self.manifest["run_id"] = "\ud800"
        result, _ = self.cli("--html-out", self.base / "report.html")
        self.assertEqual(result.returncode, 2)

    def test_extreme_timestamp_rejected_without_crash(self):
        self.manifest["started_at"] = "0001-01-01T00:00:00+23:59"
        result, _ = self.cli()
        self.assertEqual(result.returncode, 2)

    def test_cli_integrity_exit_and_reports(self):
        self.manifest["cases"][0]["exit_code"] = 1
        json_out, html_out = self.base / "report.json", self.base / "report.html"
        result, report = self.cli("--json-out", json_out, "--html-out", html_out)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(json_out.read_text(encoding="utf-8")), report)
        self.assertIn("Evidence needs attention", html_out.read_text(encoding="utf-8"))

    def test_cli_does_not_overwrite_input(self):
        result, _ = self.cli("--json-out", self.base / "prediction.json")
        self.assertEqual(result.returncode, 2)
        self.assertEqual((self.base / "prediction.json").read_bytes(), b"prediction")

    def test_unsafe_absolute_artifact_cannot_be_overwritten(self):
        artifact = self.base / "prediction.json"
        self.manifest["cases"][0]["artifact"]["path"] = str(artifact.resolve())
        for option in ("--json-out", "--html-out"):
            with self.subTest(option=option):
                result, _ = self.cli(option, artifact)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(artifact.read_bytes(), b"prediction")

    def test_traversal_artifact_cannot_be_overwritten(self):
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        artifact = Path(outside.name) / "outside.json"
        artifact.write_bytes(b"preserve me")
        self.manifest["cases"][0]["artifact"]["path"] = Path(os.path.relpath(artifact, self.base)).as_posix()
        result, _ = self.cli("--json-out", artifact)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(artifact.read_bytes(), b"preserve me")

    def test_symlink_artifact_target_cannot_be_overwritten(self):
        artifact = self.base / "prediction.json"
        link = self.base / "linked-output.json"
        try:
            link.symlink_to(artifact)
        except OSError as exc:
            self.skipTest(f"Symlink unavailable: {exc}")
        self.manifest["cases"][0]["artifact"]["path"] = link.name
        for output in (link, artifact):
            with self.subTest(output=output):
                result, _ = self.cli("--json-out", output)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(artifact.read_bytes(), b"prediction")
                self.assertTrue(link.is_symlink())

    def test_invalid_json_exit_two(self):
        result, report = self.cli(text="{")
        self.assertEqual(result.returncode, 2)
        self.assertFalse(report["input_valid"])

    def test_artifact_content_is_never_executed(self):
        marker = self.base / "executed"
        payload = f"from pathlib import Path\nPath({str(marker)!r}).touch()".encode()
        (self.base / "code.py").write_bytes(payload)
        for case in self.manifest["cases"]:
            case["artifact"].update(path="code.py", sha256=hashlib.sha256(payload).hexdigest())
        self.assertTrue(self.audit()["integrity_ok"])
        self.assertFalse(marker.exists())

    def test_html_escapes_untrusted_fields(self):
        self.manifest["run_id"] = '<script>alert("x")</script>'
        self.manifest["cases"][0]["id"] = '<img src=x onerror=alert(1)>'
        rendered = render_html(self.audit())
        self.assertNotIn("<script>", rendered)
        self.assertNotIn("<img src=", rendered)
        self.assertIn("&lt;script&gt;", rendered)
        self.assertIn("Content-Security-Policy", rendered)

    def test_json_output_paths_cannot_collide(self):
        result, _ = self.cli("--json-out", self.base / "report", "--html-out", self.base / "report")
        self.assertEqual(result.returncode, 2)


if __name__ == "__main__":
    unittest.main()
