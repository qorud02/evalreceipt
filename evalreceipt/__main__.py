"""Command line interface. Stdout always contains one JSON object."""

import argparse
import json
import os
import tempfile
from pathlib import Path
import sys

from .audit import InputError, audit_manifest, load_manifest
from .render import render_html


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".evalreceipt-", delete=False) as stream:
            name = stream.name
            stream.write(content)
        os.replace(name, path)
    finally:
        if name is not None and Path(name).exists():
            Path(name).unlink()


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise InputError(message)


def _path_aliases(path: Path, *, output: bool = False) -> set[Path]:
    """Protect both declared names and resolved targets, including unsafe input paths.

    Resolving names inspects path metadata only. Artifact bytes are read solely by
    the audit after its containment and link checks.
    """
    aliases = {Path(os.path.abspath(path))}
    try:
        aliases.add(path.resolve())
    except (ValueError, OSError, RuntimeError) as exc:
        if output:
            raise InputError(f"Cannot resolve report output path: {exc}") from exc
        # Invalid/unresolvable declarations still retain lexical collision protection.
    return aliases


def main(argv: list[str] | None = None) -> int:
    parser = JsonArgumentParser(description="Inspect execution evidence for an evaluation run")
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--json-out", type=Path, help="Save JSON report")
    parser.add_argument("--html-out", type=Path, help="Save escaped HTML report")
    try:
        args = parser.parse_args(argv)
        manifest = load_manifest(args.manifest)
        input_paths = _path_aliases(args.manifest)
        for record in manifest["cases"]:
            if record["artifact"]:
                declared = args.manifest.parent / record["artifact"]["path"]
                input_paths.update(_path_aliases(declared))
        output_paths = [_path_aliases(path, output=True) for path in (args.json_out, args.html_out) if path]
        if len(output_paths) == 2 and output_paths[0] & output_paths[1]:
            raise InputError("JSON and HTML output paths must differ")
        if any(aliases & input_paths for aliases in output_paths):
            raise InputError("Report output cannot overwrite a manifest or declared artifact")
        report = audit_manifest(manifest, args.manifest.parent)
        serialized = json.dumps(report, ensure_ascii=True, allow_nan=False, indent=2) + "\n"
        if args.json_out:
            _write(args.json_out, serialized)
        if args.html_out:
            _write(args.html_out, render_html(report))
        sys.stdout.write(serialized)
        return 0 if report["integrity_ok"] else 1
    except (InputError, OSError, RuntimeError) as exc:
        sys.stdout.write(json.dumps({"tool": "EvalReceipt", "input_valid": False, "error": str(exc)}, ensure_ascii=True) + "\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
