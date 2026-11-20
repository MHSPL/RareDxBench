"""
Trim the classification benchmark to its hardest variants.

Reads the extended set (every variant that at most 3 of 11 classifiers called
with the exact expert 5-class label), counts for each variant how many of the 8
reference classifiers got it exactly right, and keeps the variants with at most
--max-correct correct (default 0: none of them got it right). The two Haiku models and Gemini 3 Pro were in the
original 11 but are not references: small and removed models do not define what counts as hard. Writes the trimmed answers and input
files that the dashboard and scripts use as the benchmark.

The reference classifiers are fixed (REFERENCE_MODELS) and read from
dashboard/models.tsv, so models added later never change the selection.

Usage:
    python3 scripts/select_hard_variants.py              # none of the 8 correct
    python3 scripts/select_hard_variants.py --max-correct 1
    python3 scripts/select_hard_variants.py --dry-run    # print the counts only
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import (
    BASE_DIR, CLASSIFICATION_ANSWERS, CLASSIFICATION_ANSWERS_EXTENDED,
    CLASSIFICATION_INPUT, CLASSIFICATION_INPUT_EXTENDED,
)
from dashboard.loaders import load_unified_data, discover_model_files, load_model_predictions

# The 8 reference classifiers that define "hard" (keys in dashboard/models.tsv); Haiku and Gemini 3 Pro are left out.
REFERENCE_MODELS = [
    "aiva_opus_rerun", "aiva_sonnet_rerun",
    "claude_code_opus", "claude_code_sonnet",
    "aiva_gemini_flash", "aiva_gpt_sol",
    "baseline_bias", "baseline_intervar",
]


def read_jsonl(path: Path) -> list[dict]:
    with open(path, "r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def write_jsonl(path: Path, rows: list[dict]):
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def count_correct(answers_path: Path) -> dict[str, int]:
    """variant id -> number of reference classifiers whose call equals the expert label."""
    variants = load_unified_data(answers_path)
    models, _ = discover_model_files(BASE_DIR)
    missing = [k for k in REFERENCE_MODELS if k not in models]
    if missing:
        sys.exit(f"Reference models missing from dashboard/models.tsv or their results: {', '.join(missing)}")
    correct = {vid: 0 for vid in variants}
    for key in REFERENCE_MODELS:
        preds = load_model_predictions(models[key], variants)
        for vid, v in variants.items():
            p = preds.get(vid)
            if p and p["prediction"] == v["ground_truth"]:
                correct[vid] += 1
    return correct


def main():
    parser = argparse.ArgumentParser(description="Trim the classification benchmark to its hardest variants")
    parser.add_argument("--max-correct", type=int, default=0,
                        help="keep variants that at most this many of the 8 reference classifiers got right (default: 0)")
    parser.add_argument("--dry-run", action="store_true", help="print the counts, write nothing")
    args = parser.parse_args()

    correct = count_correct(CLASSIFICATION_ANSWERS_EXTENDED)
    print(f"Extended set: {len(correct)} variants")
    print("Reference classifiers correct, per variant:")
    for n, c in sorted(Counter(correct.values()).items()):
        print(f"  {n:>2} of {len(REFERENCE_MODELS)}: {c}")

    keep = {vid for vid, n in correct.items() if n <= args.max_correct}
    answers = [r for r in read_jsonl(CLASSIFICATION_ANSWERS_EXTENDED) if r["id"] in keep]
    inputs = [r for r in read_jsonl(CLASSIFICATION_INPUT_EXTENDED) if r["id"] in keep]
    assert len(answers) == len(inputs) == len(keep), "answers and input files disagree on ids"

    print(f"\nKeeping variants with at most {args.max_correct} correct: {len(keep)}")
    print("  By expert class:", dict(Counter(r["classification"] for r in answers)))
    print(f"  Genes: {len({r['gene_symbol'] for r in answers})}, panels: {len({r['expert_panel'] for r in answers})}")

    if args.dry_run:
        return
    write_jsonl(CLASSIFICATION_ANSWERS, answers)
    write_jsonl(CLASSIFICATION_INPUT, inputs)
    print(f"Wrote {CLASSIFICATION_ANSWERS}\nWrote {CLASSIFICATION_INPUT}")


if __name__ == "__main__":
    main()
