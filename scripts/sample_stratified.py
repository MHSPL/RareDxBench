"""
Draw a stratified sample of variants for a pilot or chunked benchmark run.

Stratifies across (ground-truth classification x disease category) so a small
sample still covers the label and phenotype spread of the full set. Variants
already classified successfully in a previous run are excluded, so repeated
calls walk through the cohort without repeating work.

Usage:
    python scripts/sample_stratified.py --n 100 --output scratch/pilot_100.jsonl
    python scripts/sample_stratified.py --n 500 --output scratch/chunk_02.jsonl
"""

import argparse
import csv
import json
import random
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import CLASSIFICATION_ANSWERS, CLASSIFICATION_DATA_DIR, CLASSIFICATION_RESULTS_DIR, RANDOM_SEED

DEFAULT_INPUT = CLASSIFICATION_ANSWERS
CATEGORY_MAP = CLASSIFICATION_DATA_DIR / "phenotype_categories.tsv"
RESULTS_GLOB = "*.jsonl"


def load_categories(path):
    """variant_id -> disease_category, from the curated phenotype map."""
    if not path.exists():
        return {}
    with open(path, newline="", encoding="utf-8") as f:
        return {
            row["variant_id"]: row.get("disease_category", "Unknown")
            for row in csv.DictReader(f, delimiter="\t")
        }


def already_done(result_dirs, model=None):
    """variant_ids that a previous run classified without error."""
    done = set()
    for d in result_dirs:
        if not d.exists():
            continue
        for path in d.glob(RESULTS_GLOB):
            with open(path, encoding="utf-8") as f:
                for line in f:
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if rec.get("error") or not rec.get("aiva_classification"):
                        continue
                    if model and rec.get("model_used") != model:
                        continue
                    if rec.get("variant_id"):
                        done.add(rec["variant_id"])
    return done


def stratify(variants, categories, n, seed):
    """Proportional allocation over (classification, category), largest remainder."""
    strata = defaultdict(list)
    for v in variants:
        key = (v.get("classification", "Unknown"),
               categories.get(v.get("id"), "Unknown"))
        strata[key].append(v)

    total = sum(len(pool) for pool in strata.values())
    if total == 0:
        return []

    exact = {k: len(pool) * n / total for k, pool in strata.items()}
    quota = {k: min(int(x), len(strata[k])) for k, x in exact.items()}

    # Hand out what rounding dropped, to the largest fractional parts first.
    remaining = n - sum(quota.values())
    by_remainder = sorted(strata, key=lambda k: exact[k] - int(exact[k]), reverse=True)
    for key in by_remainder:
        if remaining <= 0:
            break
        if quota[key] < len(strata[key]):
            quota[key] += 1
            remaining -= 1

    rng = random.Random(seed)
    picked = []
    for key in sorted(strata):
        if quota[key]:
            picked.extend(rng.sample(strata[key], quota[key]))
    rng.shuffle(picked)
    return picked


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--n", type=int, default=100, help="how many variants to draw")
    p.add_argument("--input", default=str(DEFAULT_INPUT), help="source JSONL")
    p.add_argument("--output", required=True, help="where to write the sample")
    p.add_argument("--results-dir", action="append", default=None,
                   help="directory of prior results to exclude (repeatable)")
    p.add_argument("--model", default=None,
                   help="only count prior results from this model as done")
    p.add_argument("--seed", type=int, default=RANDOM_SEED)
    p.add_argument("--include-done", action="store_true",
                   help="do not exclude variants already classified")
    args = p.parse_args()

    variants = [json.loads(line) for line in open(args.input, encoding="utf-8")]
    categories = load_categories(CATEGORY_MAP)

    dirs = [Path(d) for d in (args.results_dir or ["results"])]
    done = set() if args.include_done else already_done(dirs, args.model)
    pool = [v for v in variants if v.get("id") not in done]

    print(f"source          : {args.input} ({len(variants)} variants)")
    print(f"already done    : {len(done)}")
    print(f"eligible        : {len(pool)}")

    if args.n > len(pool):
        print(f"\nOnly {len(pool)} variants left; drawing all of them.")
        args.n = len(pool)

    sample = stratify(pool, categories, args.n, args.seed)

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        for v in sample:
            f.write(json.dumps(v, ensure_ascii=False) + "\n")

    labels = defaultdict(int)
    cats = defaultdict(int)
    for v in sample:
        labels[v.get("classification", "Unknown")] += 1
        cats[categories.get(v.get("id"), "Unknown")] += 1

    print(f"\nwrote {len(sample)} variants to {out}")
    print(f"genes covered   : {len({v.get('gene_symbol') for v in sample})}")
    print("\nby classification:")
    for k, c in sorted(labels.items(), key=lambda kv: -kv[1]):
        print(f"  {k:<28} {c}")
    print("\nby disease category:")
    for k, c in sorted(cats.items(), key=lambda kv: -kv[1]):
        print(f"  {k:<32} {c}")


if __name__ == "__main__":
    main()
