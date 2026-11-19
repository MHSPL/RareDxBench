# RareDx Bench

A rare disease diagnostics benchmark for AI variant interpretation. Results are on the
dashboard at https://benchmarks.aivaportal.com.

## Benchmark files

Run your model on the input file, then score it against the answers, matched by `id`.

### Variant Classification (`data/classification/`)

2,894 ClinGen eRepo variants with expert panel classifications.

| File | Contents |
|------|----------|
| `aiva_bench_input.jsonl` | Input: the variants, with no classifications |
| `erepo_bias_intervar_merged.jsonl` | Answers: eRepo expert classification and criteria, plus BIAS-2015 and InterVar predictions |

### Variant Ranking (`data/ranking/`)

1,396 simulated rare disease patients, each with one causal gene among the candidates.

| File | Contents |
|------|----------|
| `ranking_prompts.jsonl` | Input: the same cases as a ready-to-send prompt |
| `ranking_answers.jsonl` | Answers: causal gene, labeled distractor genes and phenotypes, categories |

## Start the dashboard server

```bash
python3 -m http.server 8050 --directory dashboard
```

Then open http://localhost:8050. To rebuild the dashboard after adding results,
run `python3 scripts/update_dashboard.py`; see [CLASSIFICATION_GUIDE.md](CLASSIFICATION_GUIDE.md)
for adding a classification model and [dashboard/README.md](dashboard/README.md) for the page itself.

## Contributors

Arun Boddapati of [PrimeOmicX](https://primeomicx.com/) led the benchmark runs.

## License

Copyright 2026 Mamidi Health Inc.

- **Code** (everything not listed as data below) is licensed under the
  [Apache License 2.0](LICENSE).
- **Data** (`data/`, `results/`, `dashboard/data/`, and the dataset embedded in
  `dashboard/index.html`) is licensed under
  [Creative Commons Attribution 4.0 International](LICENSE-DATA) (CC BY 4.0).
  Credit it as: *RareDx Bench, Mamidi Health and PrimeOmicX,
  https://github.com/MHSPL/RareDxBench*.

### Third-party data and assets

These keep their own terms; the licences above do not apply to them.

| Material | Source | Terms |
|---|---|---|
| Expert classifications and criteria | [ClinGen Evidence Repository](https://erepo.clinicalgenome.org/) (2 February snapshot, as distributed with the BIAS-2015 v2.0.0 supplementary files) | [CC0 1.0](https://clinicalgenome.org/docs/terms-of-use/); ClinGen asks for attribution and the date accessed |
| BIAS-2015 classifications and criteria (v3) | [bitscopic/BIAS-2015](https://github.com/bitscopic/BIAS-2015) | Tool released under AGPL-3.0; only its outputs are included here. Cite the BIAS-2015 paper ([PMC12706976](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC12706976/)) |
| InterVar classifications and criteria | [WGLab/InterVar](https://github.com/WGLab/InterVar) | Tool free for non-commercial use; only its outputs are included here |
| Inter and Source Code Pro fonts | `dashboard/fonts/` | SIL Open Font License 1.1 (texts alongside) |
| Mamidi Health logos and wordmark | `dashboard/assets/` | Trademarks of Mamidi Health Inc., all rights reserved; not covered by either licence |

## Contact

Mamidi Health Inc. Research Team
