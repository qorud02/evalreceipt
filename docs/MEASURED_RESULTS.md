# Measured experiments — 2026-10-08

EvalReceipt separates execution integrity from semantic task success. The tests include valid score-zero results and failed processes with convincing score-one output.

| Experiment | Measured outcome |
|---|---|
| Unit tests | 40 passed on Python 3.12.9, Windows |
| Execution fault matrix | 20 of 20 expected checks passed: 3 controls and 17 detected faults |
| Stored TinyQuant models | 21 independent subprocesses; all 21 accuracies matched supplied results within 1e-12 |
| Clean model receipts | 21 valid completions, 21 semantic passes at accuracy threshold 0.9 |
| One modified model copy | `hash_mismatch`; 20 of 21 valid completions |
| One removed case record | `missing_case`; 20 of 21 valid completions |
| Source preservation | All 31 source-file hashes unchanged after the stored-model experiment |

The execution run included two optional KLayout 0.30.5 scenarios. The portable default runs 18 scenarios without KLayout. See the experiment READMEs for commands.

The TinyQuant model set consists of three seeds (17, 43, 91), each with float32 and two quantization methods at 8, 4 and 3 bits. Held-out accuracy in the receipt experiment ranged from 0.9400544959128065 to 0.989100817438692. Process elapsed times include interpreter startup, imports and file IO; use TinyQuant's own benchmark results for inference latency.

[Execution summary](measured/execution_summary.json), [model receipt summary](measured/quant_receipts_summary.json) and [21 inference receipts](measured/quant_inference_receipts.json) preserve the measured outcomes and hashes. Source filenames and commands use portable identities. Archived timestamps describe the original run; regenerate fixtures and experiment outputs to audit local filesystem freshness.

The [Korean experiment report](Model_Quantization_and_Evaluation_Report_KO.pdf) presents the quantization experiment and its integration with EvalReceipt. Its directory-level reproduction commands refer to sibling `tinyquant-lab` and `evalreceipt` checkouts; use the experiment READMEs in this repository for the adapter commands.

The GitHub Actions matrix covers Ubuntu and Windows on Python 3.11–3.13. Hosted CI results will be available after the first repository run.
