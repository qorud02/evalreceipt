"""Owned fault-injection worker. It never accepts arbitrary source code."""
import argparse
import json
from pathlib import Path
import sys
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", required=True, choices=["normal", "wrong-answer", "crash-before", "crash-after", "timeout", "invalid-json", "nan-score", "native-region", "native-crash"])
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    mode = args.mode
    if mode == "timeout":
        time.sleep(3)
        return 0
    if mode == "crash-before":
        print("Injected failure before output", file=sys.stderr)
        return 7
    if mode == "invalid-json":
        args.out.write_text('{"score":', encoding="utf-8")
        return 0
    if mode == "native-crash":
        import klayout.db as kdb
        # Exercise a real native-library type error in a separate process.
        kdb.Region({"invalid": "geometry"})
        return 0
    score = 0.0 if mode == "wrong-answer" else 1.0
    detail = {"predictions": []}
    if mode == "native-region":
        import klayout.db as kdb
        a = kdb.Region(kdb.Box(0, 0, 100, 100))
        b = kdb.Region(kdb.Box(50, 0, 150, 100))
        intersection = a & b
        area = intersection.area()
        score = float(area == 5000)
        detail = {"intersection_area": area, "library": "KLayout", "expected_area": 5000}
    payload = {"score": score, "passed": score >= 1, "detail": detail}
    if mode == "nan-score":
        payload["score"] = float("nan")
    args.out.write_text(json.dumps(payload, allow_nan=True), encoding="utf-8")
    if mode == "crash-after":
        print("Injected failure after a convincing output was written", file=sys.stderr)
        return 9
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
