# EvalReceipt

Check the execution evidence behind an evaluation score.

EvalReceipt reads a run manifest and checks case coverage, process outcomes, scores, artifact timestamps, and SHA-256 hashes. It produces JSON for CI and an optional HTML report for review. A valid score of zero stays visible as a semantic failure. A crashed process cannot become a successful case through an empty output.

## Run locally

Python 3.11 or newer. The audit, report renderer, fixture generator, and tests use the standard library. Commands below run directly from this directory, without installing a package.

```powershell
python scripts/make_fixtures.py
python -m evalreceipt fixtures/clean.json --json-out reports/clean.json --html-out reports/clean.html
python -m evalreceipt fixtures/failed-empty-output.json --json-out reports/failed.json --html-out reports/failed.html
python -m unittest discover -s tests -v
```

The fixture generator creates fresh local artifacts and receipts. Run it after copying or checking out the project because the audit checks real filesystem modification times. The `failed-empty-output` fixture records a nonzero process exit and an empty output while its claimed semantic score is perfect; the audit excludes it from valid completion.

For a package install, `pip install .` exposes the same interface as `evalreceipt`. The packaging step uses setuptools; runtime checks need no external packages.

## Use with your evaluator

Record your expected case IDs before execution. For every attempt, store the process status, return code, timeout flag, score, semantic pass flag, and output receipt. Put the manifest beside its output directory, then audit it:

```powershell
python -m evalreceipt path/to/run/manifest.json --json-out review.json --html-out review.html
```

The CLI writes one JSON object to stdout and uses these exit codes:

| Code | Meaning |
|---|---|
| 0 | Execution evidence is consistent; semantic failures can still exist |
| 1 | Evidence has integrity findings |
| 2 | Input or requested report output is invalid |

`--help` displays usage. Reports cannot overwrite the input manifest or a declared artifact. Paths for JSON and HTML outputs must differ.

## Checks

| Evidence problem | Treatment |
|---|---|
| Failed process or timeout | Exclude the case from valid completion |
| Missing, duplicate, or extra case | Preserve expected denominator; report the inconsistent records |
| Nonfinite score | Reject malformed input |
| Finite score outside [0,1] or pass-flag disagreement | Report a case finding |
| Empty expected-case list | Report a finding; rates become null |
| Missing or stale artifact, changed bytes, hash mismatch | Report a case finding |
| Parent traversal, absolute paths, symbolic links, or junctions | Refuse the artifact path |
| Untrusted strings in HTML | Escape the displayed values |

See [SCHEMA.md](SCHEMA.md) for the complete contract and statistic definitions, and [manifest.schema.json](manifest.schema.json) for its machine-readable structure.

## Fault-injection fixtures

The generator produces clean evidence, a valid semantic failure, failed empty output, timeout, missing case, duplicate case, extra case, out-of-range score, hash mismatch, missing artifact, stale timestamp, empty denominator, path traversal, and nonfinite input. Each run has a stable scenario name and current local artifact timestamps.

## Research context

The implementation studies execution evidence as a separate layer from model performance. Its motivation includes the execution-failure issue documented in [Rule2DRC PR #4](https://github.com/snu-mllab/Rule2DRC/pull/4). This package implements its own manifest contract, audits, renderer, and fixtures. The Rule2DRC repository and VibeCite are reference projects for inspectable tools and reproducible project presentation.

The report audits consistency between a supplied manifest and local artifacts. Task scores remain the evaluator's input. For reliable use, capture receipts through a trusted collector and audit immutable run directories. External benchmark adoption and independently reviewed results can be recorded as the project develops.

## Measured experiments

The execution fault matrix passed 20 expected checks, including two optional native KLayout cases. A separate adapter ran all 21 saved TinyQuant models in subprocesses, reproduced their held-out accuracy, and detected modified model bytes and a missing case record. See [measured results](docs/MEASURED_RESULTS.md), the [execution experiment](experiments/eval_execution/README.md), and the [stored-model adapter](experiments/quant_receipts/README.md).

The default execution experiment needs no dependencies:

```sh
python experiments/eval_execution/run_experiment.py --output-dir generated-experiments/execution-01
```

The core package is distributed under the [MIT License](LICENSE). Citation metadata is in [CITATION.cff](CITATION.cff). The included wheel installs offline with `python -m pip install --no-index --no-deps dist/evalreceipt-0.1.0-py3-none-any.whl`.
