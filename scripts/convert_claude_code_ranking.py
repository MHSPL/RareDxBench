#!/usr/bin/env python3
"""Convert Claude Code ranking responses (311 cases) into dashboard prediction JSONL files.

Inputs (results/ranking/Claude_code_results/):
  haiku5.5_high_responses.json, sonnet5.5_high_responses.json  dict keyed by patient id
  opus5.5_high_responses.json                                  list with a raw_response JSON string

Outputs: results/ranking/Results_Claude_Code_<Model>/claude_code_<model>_311_predictions.jsonl
"""
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from scripts.run_opus_ranking import find_rank  # noqa: E402

SRC = REPO / "results" / "ranking" / "Claude_code_results"
MODELS = [
    ("haiku", "Haiku_5_5", "haiku5.5_high_responses.json", "anthropic/claude-haiku-5-5"),
    ("sonnet", "Sonnet_5_5", "sonnet5.5_high_responses.json", "anthropic/claude-sonnet-5-5"),
    ("opus", "Opus_5_5", "opus5.5_high_responses.json", "anthropic/claude-opus-5-5"),
]


def parse_response(obj):
    """Return (predicted symbols, score map) from one response."""
    if "raw_response" in obj:
        obj = json.loads(obj["raw_response"])
    preds = obj.get("predicted_genes") or []
    genes, scores = [], {}
    for p in preds:
        g = (p.get("gene") or "").strip()
        if g and g not in scores:
            genes.append(g)
            scores[g] = p.get("score", 0.0)
    return genes, scores


def main():
    with open(REPO / "data/ranking/selected_311_patients.json") as f:
        pids = [int(x) for x in json.load(f)["patient_ids"]]
    answers = {}
    with open(REPO / "data/ranking/ranking_answers_311.jsonl") as f:
        for line in f:
            if line.strip():
                a = json.loads(line)
                answers[int(a["id"])] = a
    with open(REPO / "results/ranking/curated_ranking_tiers.tsv") as f:
        tiers = {int(r["patient_id"]): r for r in csv.DictReader(f, delimiter="\t")}

    for key, folder, fname, model_id in MODELS:
        with open(SRC / fname) as f:
            raw = json.load(f)
        by_pid = {int(r["id"]): r for r in raw} if isinstance(raw, list) else {int(k): v for k, v in raw.items()}
        out_dir = REPO / "results" / "ranking" / f"Results_Claude_Code_{folder}"
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / f"claude_code_{key}_311_predictions.jsonl"
        n_found = n_empty = 0
        with open(out, "w", encoding="utf-8") as f:
            for pid in pids:
                a, t = answers[pid], tiers[pid]
                genes, scores = parse_response(by_pid[pid]) if pid in by_pid else ([], {})
                true_sym = [t["true_gene_symbol"]]
                rank = find_rank(genes, true_sym) if genes else None
                n_found += rank is not None
                n_empty += not genes
                f.write(json.dumps({
                    "patient_id": pid, "disease_id": a["disease_id"], "ablation": "hpo+vcf+genes+disease",
                    "model": model_id, "true_genes": true_sym, "true_genes_ensg": a["true_genes"],
                    "predicted": genes, "predicted_scores": scores, "rank_of_true_gene": rank,
                    "category": a["category"], "broad_category": a["broad_category"],
                    "in_gene_gene_kg": a["in_gene_gene_kg"], "in_disease_gene_kg": a["in_disease_gene_kg"],
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }, ensure_ascii=False) + "\n")
        print(f"{key}: {len(pids)} cases, true gene found {n_found}, empty {n_empty} -> {out.relative_to(REPO)}")


if __name__ == "__main__":
    main()
