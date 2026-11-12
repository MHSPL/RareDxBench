# RareDx Bench dashboard

`index.html` is a self-contained page: open it directly, or run the local
server for editing. It is published at https://benchmarks.aivaportal.com by
`.github/workflows/pages.yml`, which deploys `index.html` to GitHub Pages
whenever it changes on `main`.

`index.html` is generated, so do not edit it by hand. Make page changes in the
source files below, then run `python3 dashboard/generate_html.py`, which inlines
the CSS and JS, fills the template's placeholders (`__FONT_FACES__`,
`__MAMIDI_LOGO__`, `__MAMIDI_ICON__`, `__MAMIDI_WORDMARK__`, `__BENCHMARK_DATA__`) and writes
`index.html`. Edits made only to `index.html` are lost the next time it is
generated.

| File | Contents |
|---|---|
| `template.html` | Page skeleton: head, header, views, footer, and the script's startup wiring |
| `css/base.css` | Theme tokens, header, view bar, cards, controls, tables, tooltip, dialog |
| `css/views.css` | Footer, Overall and Ranking sections, model pickers, leaderboard, criteria grid, phone layout, figure export |
| `js/core.js` | Constants, helpers, tooltip, footer, model naming and selection |
| `js/leaderboard.js` | Leaderboard table, Overall snapshot, accuracy against safety scatter |
| `js/views.js` | Categories, confusion matrices, ACMG criteria, agreement heatmap, variants CSV, tabs and URL |
| `js/figure-svg.js` | Redraws a figure card as editable SVG |
| `js/figure-files.js` | TIFF encoding, downloads, and the export menu |

The template pulls them in with include lines (`/* @include css/base.css */`,
`// @include js/core.js`). All JS files run inside one closure, in the order the
template lists them, so they share constants and functions. The data build
(`build_data.py`) is split the same way: `loaders.py`, `scoring.py`,
`variant_table.py` and `archive.py`.

Three sections: **Overall** (`#overall`), **Variant Classification**
(everything below) and **Variant Ranking** (`#ranking`).

**Overall** is the landing page and a snapshot of each benchmark: the
classification leaderboard cut to its Overall, P/LP, VUS and B/LB rows
(3-class, every model, no model links or filters), paired with the variant
ranking snapshot displaying Recall@1, Recall@5, Recall@10, Recall@20, MRR,
NDCG@10 and Median Rank across all 1,396 rare disease cases.

**Variant Ranking** (`#ranking`) presents the full gene prioritization
leaderboard with overall performance and category breakdowns across five
biological difficulty tiers (established gene-disease pairs, known gene with
extended KG, known gene with new disease, novel gene with known disease, and
novel gene with novel disease). Models are grouped into AIVA, Agents (Claude Haiku 5.5),
and Automated tools (LIRICAL, ClinPrior, Exomiser).

Variant Classification has three tabs:

- **Leaderboard**: F1 by expert class and disease category with models
  grouped by section (AIVA, Agents, Automated), the accuracy-against-safety
  scatter, and the pairwise agreement heatmap. "Choose models" filters all three.
- **Confusion matrices**: per model, with precision and recall by class.
- **ACMG criteria**: share of variants citing each criterion, pathogenic
  evidence above benign evidence.

**Download variants (CSV)** in the header exports every benchmark variant
(gene, HGVS, variant key, disease, category, expert panel, expert
classification, criteria and points) with one column per model holding its
call.

## Adding a model

1. Put the results file in the repository (AIVA JSONL output, or a Claude
   Code JSON array).
2. Add one line to `models.tsv` (tab-separated):

   | column | example |
   |---|---|
   | key | `aiva_gpt_sol` |
   | name | `GPT-6.1 Sol` (the model name only; the section shows how it was run) |
   | provider | `Anthropic`, `OpenAI`, `Google`, `Rule-based` or `Other` |
   | method | `AIVA agent` |
   | path | `results/classification/Results_GPT_Sol_Analysis/sol_full.jsonl` |
   | format | `jsonl`, `json_array`, or `auto` (by extension) |
   | color | optional; empty takes the next unused colour of the model palette |
   | color_dark | optional dark-theme colour; empty takes the palette's pair for `color` |
   | status | `active`, or `removed` to hide the model |
   | section | leaderboard section: `aiva`, `agent` (Claude Code, Antigravity, …) or `automated` (rule-based); empty derives it |

3. Rebuild and commit:

   ```bash
   python scripts/update_dashboard.py
   git add dashboard/models.tsv dashboard/data/benchmark_data.json dashboard/index.html <results file>
   ```

Put results files under `results/classification/`; git tracks JSON and JSONL
there automatically and ignores them elsewhere.

## Benchmark variant set

The dashboard scores every model on `data/classification/erepo_bias_intervar_merged.jsonl`:
2,894 ClinGen eRepo variants, each with the expert classification and the
BIAS-2015 and InterVar predictions. They are the variants of the full 8,387
cohort that at most 3 of 11 reference classifiers called with the exact expert
5-class label (AIVA Opus, Sonnet, Haiku, Gemini 3 Pro, Gemini 3.8 Flash and
GPT-6.1 Sol; Claude Code Opus, Sonnet and Haiku; BIAS-2015; InterVar), so the
set concentrates on variants that are hard to classify. It is fixed: models
added later run on these same variants.

The BIAS-2015 calls and criteria are from BIAS-2015 v3 (3.0.0), taken from
`MHSPL/AIVA-var-bench` (commit `b44327e`) for these 2,894 variants. The set
itself was selected with the earlier v2.0.0 calls and was not re-selected.

`data/classification/aiva_bench_input.jsonl` holds the same 2,894 variants without any
classification (expert, BIAS-2015 or InterVar), for running a model or sharing
the benchmark; results are matched to the answers by `id`.

## Preview locally

`index.html` holds every result, so it opens directly in a browser. To serve it:

```bash
python3 -m http.server 8050 --directory dashboard
```

then visit http://localhost:8050. Model changes (adding one, or setting
`status` to `removed` in `models.tsv`) go through a pull request that rebuilds
the dataset and `index.html`; see "Adding a model" above.

## Model colours

Each model has one colour, used in every chart, table, legend and swatch. The
colours come from the Wes Anderson palettes (`PALETTE` in `registry.py`, from
the wesanderson R package) and are fixed per model in `models.tsv`; models
added later take the next unused palette entry.
In the dark theme, colours below 3:1 contrast on the dark surface use a
lighter step of the same hue.

Models are named without their method, so a model run through AIVA and
through Claude Code shares a name (e.g. Opus 5.5). The leaderboard's section
columns tell them apart; elsewhere, where both appear together, the method is
added in brackets, e.g. "Opus 5.5 (Claude Code)".

## Theme and fonts

Colours and type follow mamidi.ai: Inter and Source Code Pro, deep green
text, a beige page and an orange accent; the dark theme uses the site's deep
green. The fonts' Latin subsets are in `fonts/` (SIL Open Font License 1.1,
licence texts alongside) and `generate_html.py` embeds them, so the page
works offline and exported SVG and TIFF figures keep the same type. The
Mamidi Health emblem in the header and browser tab comes from `assets/`.
