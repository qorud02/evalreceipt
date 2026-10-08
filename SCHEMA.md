# Manifest contract

`manifest.schema.json` describes version 1 using JSON Schema 2020-12. EvalReceipt implements the structural checks itself with the Python standard library. Object keys must be unique. Unknown fields, missing fields, booleans in numeric positions, nonfinite numbers, and duplicate IDs in `expected_cases` produce exit code 2.

## Required fields

| Field | Type | Meaning |
|---|---|---|
| `schema_version` | integer 1 | Contract version |
| `run_id` | nonempty string | Human-readable run identifier |
| `started_at` | timestamp with UTC offset | Start of the execution interval |
| `finished_at` | timestamp with UTC offset | End of the execution interval, at or after start |
| `pass_threshold` | finite number in [0,1] | Minimum score for a semantic pass |
| `expected_cases` | unique string array | Cases that must be present |
| `cases` | record array | Actual execution receipts |

Each case contains all of the following fields:

| Field | Type | Meaning |
|---|---|---|
| `id` | nonempty string | Expected case ID |
| `status` | `completed`, `failed`, or `timeout` | Recorded execution outcome |
| `exit_code` | integer or null | Process return code |
| `timed_out` | boolean | Whether the process exceeded its time budget |
| `score` | finite number or null | Task score, expected to be in [0,1] |
| `passed` | boolean or null | Semantic result under `pass_threshold` |
| `artifact` | artifact object or null | Output receipt |

An artifact contains `path` (relative POSIX path), `sha256` (64 hexadecimal characters), and `created_at` (timestamp with UTC offset).

## Structural errors and evidence findings

Malformed JSON and contract violations return exit code 2. JSON `NaN`, `Infinity`, and overflowed numeric values such as `1e999` are rejected. Finite out-of-range scores are evidence findings and return code 1. Nullable fields allow a failed run to describe its missing output honestly; a null score, pass flag, or artifact cannot contribute to valid completion.

Case IDs are exact, case-sensitive strings. Duplicate actual records invalidate every occurrence of that ID. Extra records remain visible but never enter valid completion or pass totals. A missing expected case remains in the denominator. Empty expected-case lists produce a finding and null rates.

A valid completed case has status `completed`, exit code 0, no timeout, a score in [0,1], a matching pass flag, and a readable artifact with the stated SHA-256. Both its declared artifact timestamp and actual filesystem modification time must fall inside the run interval, including its endpoints. Artifact paths with absolute prefixes, drives, backslashes, parent traversal, alternate-stream separators, symbolic links, or Windows junctions are rejected. Hashing reads the file through one descriptor and checks whether its size or modification time changed during the read.

The HTML renderer escapes run identifiers, case IDs, findings, and other displayed values. It contains no executable scripts, remote resources, or artifact links.

Report destinations are compared with the manifest and every declared artifact path before writing. This comparison protects both the lexical name and its resolved target, including absolute, parent-traversal, and symbolic-link declarations that the audit will reject. Resolving a declared name inspects path metadata; it does not read the artifact's contents. A collision returns exit code 2 and leaves the inputs intact.

## Report statistics

`expected` is the denominator for `completion_rate` and `success_rate`. `valid_completed` counts expected IDs with exactly one valid record. `semantic_passed` counts valid records whose pass flag is true. `semantic_failed` counts valid records whose pass flag is false. `mean_valid_score` uses valid completed records only and is null when there are none. Every rejected record keeps its original status, score, and pass flag in the report for inspection.

The report checks recorded execution evidence. Scores and semantic flags are supplied by the evaluator; the tool does not recalculate task correctness. The manifest and directory should be captured by a trusted collector and kept immutable while auditing. SHA-256 identifies the inspected bytes; timestamps and local hashes do not authenticate their author.
