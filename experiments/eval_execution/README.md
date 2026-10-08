# Execution fault matrix

Run from the repository root with Python 3.11 or newer:

```sh
python experiments/eval_execution/run_experiment.py --output-dir generated-experiments/execution-01
```

This launches the repository's fixed worker modes in separate processes. The default matrix contains 18 scenarios: two controls and 16 injected integrity faults. It needs only the Python standard library. A successful process with score zero is the semantic-failure control.

To add two native-library scenarios, supply a Python interpreter with KLayout installed:

```sh
python experiments/eval_execution/run_experiment.py --output-dir generated-experiments/execution-native-01 --klayout-python /path/to/python
```

The native control checks an intersection area of 5,000 square database units; the native failure triggers a type error before an output exists. Each run writes manifests, process records, JSON audits and HTML reports. Choose a new output directory for every run.

The measured Windows run included KLayout 0.30.5 and passed all 20 expected checks. Its summary is in [execution_summary.json](../../docs/measured/execution_summary.json).
