"""Verify an installed wheel from an isolated working directory."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("target", type=Path)
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args()
    target = args.target.resolve()
    manifest = args.manifest.resolve()
    with tempfile.TemporaryDirectory() as working:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(target)
        code = "import evalreceipt,importlib.metadata,json; print(json.dumps({'module':evalreceipt.__file__,'version':importlib.metadata.version('evalreceipt')}))"
        metadata = subprocess.run([sys.executable, "-c", code], cwd=working, env=env,
                                  capture_output=True, text=True, timeout=30, check=True)
        info = json.loads(metadata.stdout)
        if not Path(info["module"]).resolve().is_relative_to(target):
            raise SystemExit("Imported source outside installed target")
        result = subprocess.run([sys.executable, "-m", "evalreceipt", str(manifest)], cwd=working,
                                env=env, capture_output=True, text=True, timeout=30)
        report = json.loads(result.stdout)
        if result.returncode != 0 or not report.get("integrity_ok"):
            raise SystemExit("Installed CLI rejected the clean control")
        unsafe_artifact = Path(working) / "declared-artifact.json"
        original_bytes = b"preserve this artifact"
        unsafe_artifact.write_bytes(original_bytes)
        unsafe_manifest = json.loads(manifest.read_text(encoding="utf-8"))
        unsafe_manifest["cases"][0]["artifact"]["path"] = str(unsafe_artifact.resolve())
        unsafe_manifest_path = Path(working) / "unsafe-manifest.json"
        unsafe_manifest_path.write_text(json.dumps(unsafe_manifest), encoding="utf-8")
        collision = subprocess.run([sys.executable, "-m", "evalreceipt", str(unsafe_manifest_path),
                                    "--json-out", str(unsafe_artifact)], cwd=working, env=env,
                                   capture_output=True, text=True, timeout=30)
        collision_json = json.loads(collision.stdout)
        preserved = unsafe_artifact.read_bytes() == original_bytes
        if collision.returncode != 2 or not preserved:
            raise SystemExit("Installed CLI failed unsafe-artifact collision protection")
        print(json.dumps({**info, "exit_code": result.returncode,
                          "integrity_ok": report["integrity_ok"], "summary": report["summary"],
                          "unsafe_artifact_collision": {"exit_code": collision.returncode,
                                                        "preserved": preserved,
                                                        "error": collision_json.get("error")}}, indent=2))
