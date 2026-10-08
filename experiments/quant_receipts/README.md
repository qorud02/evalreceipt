# Stored-model receipt adapter

This experiment runs TinyQuant's saved models in separate processes, computes held-out accuracy from their prediction matrices, and audits model copies with EvalReceipt. It does not call training.

Place the [TinyQuant repository](https://github.com/qorud02/tinyquant-lab) beside this checkout, or pass its directory explicitly. Install its NumPy requirement in the Python environment used for the adapter. Supply a completed TinyQuant result directory containing `results.json`, each seed's `split.npz` and `metadata.json`, and the 21 model archives. The TinyQuant checkout also supplies its dataset cache.

```sh
python -m pip install -r ../tinyquant-lab/requirements.txt
python experiments/quant_receipts/run_adapter.py --tinyquant-root ../tinyquant-lab --results ../tinyquant-lab/results/results.json --out experiments/quant_receipts/runs/reproduction-01
```

The default result path is `../tinyquant-lab/results/results.json`. A downloaded result directory can be supplied with `--results`; the code and dataset directory still come from `--tinyquant-root`.

The adapter checks final randomized round-robin timing metadata, dataset and split hashes, and uniqueness of all 21 model identities. It copies models during the recorded run window, invokes only TinyQuant's fixed `run` subcommand, and independently computes accuracy with threshold 0.9. It then creates separate tampered-model and missing-record scenarios. Existing output directories are preserved; select a fresh child directory under `experiments/quant_receipts`.

JSON and HTML audits are written for all three scenarios. `provenance.json` records relative source identities, model and prediction hashes, timestamps, subprocess outcomes and accuracy comparisons. The measured run passed 21 of 21 accuracy comparisons, detected `hash_mismatch` after changing one model, and detected `missing_case` after removing one record. See [quant_receipts_summary.json](../../docs/measured/quant_receipts_summary.json) and [quant_inference_receipts.json](../../docs/measured/quant_inference_receipts.json).
