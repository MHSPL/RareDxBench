"""
Scoring and data assembly for the RareDx Bench variant ranking benchmark.
"""

import csv
import json
import math
from pathlib import Path
from collections import defaultdict

CATEGORY_ORDER = [
    "known_gene_disease",
    "known_gene_known_disease",
    "known_gene_new_disease",
    "new_gene_known_disease",
    "new_gene_new_disease"
]

CATEGORY_LABELS = {
    "known_gene_disease": "Established cases (known gene & disease)",
    "known_gene_known_disease": "Known gene, known disease (extended KG)",
    "known_gene_new_disease": "Known gene, new disease association",
    "new_gene_known_disease": "Novel gene, known disease",
    "new_gene_new_disease": "Novel gene & novel disease"
}

CATEGORY_COUNTS = {
    "known_gene_disease": 422,
    "known_gene_known_disease": 234,
    "known_gene_new_disease": 244,
    "new_gene_known_disease": 246,
    "new_gene_new_disease": 250
}


def read_ranking_registry(repo_root: Path) -> list[dict]:
    reg_file = repo_root / "dashboard" / "ranking_models.tsv"
    if not reg_file.exists():
        return []
    models = []
    columns = ["key", "name", "provider", "method", "path", "format", "color", "color_dark", "status", "section"]
    for line in reg_file.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        parts = line.split("\t") + [""] * len(columns)
        row = dict(zip(columns, (p.strip() for p in parts[:len(columns)])))
        if row.get("status") == "removed":
            continue
        models.append(row)
    return models


def load_csv_metrics(csv_path: Path) -> dict:
    """Map tool -> category -> dict of metrics from comparison_metrics.csv."""
    data = defaultdict(dict)
    if not csv_path.exists():
        return data
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            tool = row["tool"]
            cat = row["category"]
            data[tool][cat] = {
                "n": int(row["n"]),
                "top1": int(row["top1"]),
                "top1_pct": float(row["top1_pct"]),
                "top3": int(row["top3"]),
                "top3_pct": float(row["top3_pct"]),
                "top5": int(row["top5"]),
                "top5_pct": float(row["top5_pct"]),
                "top10": int(row["top10"]),
                "top10_pct": float(row["top10_pct"]),
                "top20": int(row["top20"]),
                "top20_pct": float(row["top20_pct"]),
                "mrr": float(row["mrr"]),
                "gene_absent": int(row.get("gene_absent", 0)),
                "gene_absent_pct": float(row.get("gene_absent_pct", 0.0)),
                "ndcg10": float(row.get("ndcg10", 0.0)),
                "median_rank": float(row.get("median_rank", 1.0))
            }
    return data


def compute_jsonl_metrics(jsonl_path: Path) -> dict:
    """Compute per-category and overall metrics from a prediction jsonl file."""
    if not jsonl_path.exists():
        return {}
    cats = defaultdict(list)
    with open(jsonl_path, "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            item = json.loads(line)
            # Find rank of true gene
            rank = item.get("rank_of_true_gene")
            if rank is None and "rank" in item:
                rank = item["rank"]
            bc = item.get("broad_category")
            if not bc:
                # Fallback to category
                c = item.get("category", "")
                for k in CATEGORY_ORDER:
                    if k in c:
                        bc = k
                        break
            cats["OVERALL"].append(rank)
            if bc:
                cats[bc].append(rank)

    results = {}
    for cat_name, ranks in cats.items():
        n = len(ranks)
        if n == 0:
            continue
        top1 = sum(1 for r in ranks if r is not None and r <= 1)
        top3 = sum(1 for r in ranks if r is not None and r <= 3)
        top5 = sum(1 for r in ranks if r is not None and r <= 5)
        top10 = sum(1 for r in ranks if r is not None and r <= 10)
        top20 = sum(1 for r in ranks if r is not None and r <= 20)
        absent = sum(1 for r in ranks if r is None)
        mrr = sum(1.0 / r for r in ranks if r is not None and r > 0) / n
        ndcg10 = sum((1.0 / math.log2(r + 1)) for r in ranks if r is not None and 1 <= r <= 10) / n
        found = sorted([r for r in ranks if r is not None])
        med = float(found[len(found) // 2]) if found else 1.0
        results[cat_name] = {
            "n": n,
            "top1": top1,
            "top1_pct": round(top1 * 100.0 / n, 2),
            "top3": top3,
            "top3_pct": round(top3 * 100.0 / n, 2),
            "top5": top5,
            "top5_pct": round(top5 * 100.0 / n, 2),
            "top10": top10,
            "top10_pct": round(top10 * 100.0 / n, 2),
            "top20": top20,
            "top20_pct": round(top20 * 100.0 / n, 2),
            "mrr": round(mrr, 4),
            "gene_absent": absent,
            "gene_absent_pct": round(absent * 100.0 / n, 2),
            "ndcg10": round(ndcg10, 4),
            "median_rank": med
        }
    return results


def build_ranking_data(repo_root: Path) -> dict:
    """Build the ranking benchmark dataset to embed into benchmark_data.json."""
    registry = read_ranking_registry(repo_root)
    csv_path = repo_root / "results" / "ranking" / "comparison_metrics.csv"
    csv_metrics = load_csv_metrics(csv_path)

    models_summary = []
    categories_data = {}

    for row in registry:
        key = row["key"]
        name = row["name"]
        rel_path = row["path"]
        file_path = repo_root / rel_path

        # Find metrics: check CSV first by name, or compute from jsonl
        tool_metrics = csv_metrics.get(name, {})
        if not tool_metrics and name == "Haiku 5.5":
            tool_metrics = csv_metrics.get("Haiku 5.5", {})
        if not tool_metrics and name == "AIVA":
            tool_metrics = csv_metrics.get("AIVA", {})

        if not tool_metrics and file_path.exists():
            computed = compute_jsonl_metrics(file_path)
            if computed:
                tool_metrics = computed

        overall = tool_metrics.get("OVERALL", {})
        if not overall and file_path.exists():
            computed = compute_jsonl_metrics(file_path)
            if computed:
                tool_metrics.update(computed)
                overall = tool_metrics.get("OVERALL", {})

        if not overall:
            continue

        model_obj = {
            "key": key,
            "name": name,
            "short_name": name,
            "family": name if row["provider"] == "Rule-based" else ("Claude" if "Haiku" in name else "AIVA"),
            "provider": row["provider"],
            "type": row["method"],
            "section": row.get("section", "automated"),
            "total_cohort": 1396,
            "evaluated_count": overall.get("n", 1396),
            "color": row.get("color", "#7FC0C6"),
            "color_dark": row.get("color_dark", row.get("color", "#7FC0C6")),
            "top1_pct": overall.get("top1_pct", 0.0),
            "top3_pct": overall.get("top3_pct", 0.0),
            "top5_pct": overall.get("top5_pct", 0.0),
            "top10_pct": overall.get("top10_pct", 0.0),
            "top20_pct": overall.get("top20_pct", 0.0),
            "mrr": overall.get("mrr", 0.0),
            "ndcg10": overall.get("ndcg10", 0.0),
            "median_rank": overall.get("median_rank", 1.0),
            "per_category": {}
        }

        cat_dict = {}
        for cat in CATEGORY_ORDER:
            c_metrics = tool_metrics.get(cat, {})
            if c_metrics:
                model_obj["per_category"][cat] = c_metrics
                cat_dict[cat] = c_metrics

        models_summary.append(model_obj)
        categories_data[key] = cat_dict

    # Sort models by MRR descending
    models_summary.sort(key=lambda m: m["mrr"], reverse=True)

    return {
        "metadata": {
            "title": "RareDx Bench: Variant Ranking",
            "subtitle": "Gene prioritization benchmark across 1,396 simulated rare-disease patient cases",
            "total_cohort": 1396,
            "categories": CATEGORY_ORDER,
            "category_labels": CATEGORY_LABELS,
            "category_counts": CATEGORY_COUNTS
        },
        "models": models_summary,
        "categories_data": categories_data
    }
