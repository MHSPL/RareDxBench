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

CATEGORY_COUNTS_FULL = {
    "known_gene_disease": 422,
    "known_gene_known_disease": 234,
    "known_gene_new_disease": 244,
    "new_gene_known_disease": 246,
    "new_gene_new_disease": 250
}

CATEGORY_COUNTS_311 = {
    "known_gene_disease": 54,
    "known_gene_known_disease": 74,
    "known_gene_new_disease": 41,
    "new_gene_known_disease": 74,
    "new_gene_new_disease": 68
}

TIERS_COL_MAP = {
    "aiva_gpt_sol": "sol_rank",
    "aiva_sonnet": "sonnet_rank",
    "aiva_opus": "opus_rank",
    "aiva_gemini_pro": "gemini_rank",
    "aiva_haiku": "haiku_rank",
    "baseline_lirical": "lirical_rank",
    "baseline_clinprior": "clinprior_rank",
    "baseline_exomiser": "exomiser_rank",
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


def compute_metrics_from_ranks(cats: dict[str, list[int | None]]) -> dict:
    """Compute overall and per-category metrics from a dictionary of category -> ranks."""
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


def compute_jsonl_metrics(jsonl_path: Path, patient_filter: set = None) -> dict:
    """Compute per-category and overall metrics from a prediction jsonl file."""
    if not jsonl_path.exists():
        return {}
    cats = defaultdict(list)
    with open(jsonl_path, "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            item = json.loads(line)
            pid = item.get("patient_id")
            if patient_filter is not None and pid not in patient_filter:
                continue
            rank = item.get("rank_of_true_gene")
            if rank is None and "rank" in item:
                rank = item["rank"]
            if rank is None and "lirical_rank" in item:
                rank = item["lirical_rank"]
            if rank is None and "clinprior_rank" in item:
                rank = item["clinprior_rank"]
            if rank is None and "exomiser_rank" in item:
                rank = item["exomiser_rank"]
            bc = item.get("broad_category")
            if not bc:
                c = item.get("category", "")
                for k in CATEGORY_ORDER:
                    if k in c:
                        bc = k
                        break
            cats["OVERALL"].append(rank)
            if bc:
                cats[bc].append(rank)

    return compute_metrics_from_ranks(cats)


def build_ranking_data(repo_root: Path) -> dict:
    """Build the ranking benchmark dataset to embed into benchmark_data.json."""
    registry = read_ranking_registry(repo_root)
    csv_path = repo_root / "results" / "ranking" / "comparison_metrics.csv"
    csv_metrics = load_csv_metrics(csv_path)

    challenge_path = repo_root / "data" / "ranking" / "selected_311_patients.json"
    pids_311 = set()
    if challenge_path.exists():
        with open(challenge_path, "r", encoding="utf-8") as f:
            pids_311 = set(int(x) for x in json.load(f)["patient_ids"])

    tiers_path = repo_root / "results" / "ranking" / "curated_ranking_tiers.tsv"
    tiers_rows = []
    if tiers_path.exists():
        with open(tiers_path, "r", encoding="utf-8") as f:
            tiers_rows = list(csv.DictReader(f, delimiter="\t"))

    # -------------------------------------------------------------------------
    # 1. BUILD 311 CHALLENGE COHORT (All 12 models)
    # -------------------------------------------------------------------------
    models_311 = []
    categories_311 = {}

    for row in registry:
        key = row["key"]
        name = row["name"]
        rel_path = row["path"]
        file_path = repo_root / rel_path

        tool_metrics = {}
        if key in TIERS_COL_MAP and tiers_rows:
            col = TIERS_COL_MAP[key]
            cats = defaultdict(list)
            for r in tiers_rows:
                if int(r["patient_id"]) in pids_311:
                    val = r.get(col, "")
                    try:
                        val_int = int(val) if val and val != "None" else None
                    except ValueError:
                        val_int = None
                    cats["OVERALL"].append(val_int)
                    bc = r.get("broad_category")
                    if bc:
                        cats[bc].append(val_int)
            tool_metrics = compute_metrics_from_ranks(cats)
        elif file_path.exists():
            tool_metrics = compute_jsonl_metrics(file_path, pids_311 if pids_311 else None)

        overall = tool_metrics.get("OVERALL", {})
        if not overall:
            continue

        model_obj = {
            "key": key,
            "name": name,
            "short_name": name,
            "family": row["provider"] if row["provider"] in ("Anthropic", "Google", "OpenAI", "Rule-based") else "Other",
            "provider": row["provider"],
            "type": row["method"],
            "section": row.get("section", "automated"),
            "total_cohort": 311,
            "evaluated_count": overall.get("n", 311),
            "is_full": True,
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

        models_311.append(model_obj)
        categories_311[key] = cat_dict

    models_311.sort(key=lambda m: m["mrr"], reverse=True)

    # -------------------------------------------------------------------------
    # 2. BUILD FULL 1,396 COHORT
    # -------------------------------------------------------------------------
    models_1396 = []
    categories_1396 = {}

    for row in registry:
        key = row["key"]
        name = row["name"]
        rel_path = row["path"]
        file_path = repo_root / rel_path

        tool_metrics = csv_metrics.get(name, {})
        if not tool_metrics and name in ("Gemini 3 Pro", "AIVA"):
            tool_metrics = csv_metrics.get("Gemini 3 Pro") or csv_metrics.get("AIVA", {})

        is_full_eval = bool(tool_metrics)
        if not tool_metrics and file_path.exists():
            computed = compute_jsonl_metrics(file_path)
            if computed:
                tool_metrics = computed
                is_full_eval = tool_metrics.get("OVERALL", {}).get("n", 0) >= 1396

        overall = tool_metrics.get("OVERALL", {})
        if not overall:
            continue

        model_obj = {
            "key": key,
            "name": name,
            "short_name": name,
            "family": row["provider"] if row["provider"] in ("Anthropic", "Google", "OpenAI", "Rule-based") else "Other",
            "provider": row["provider"],
            "type": row["method"],
            "section": row.get("section", "automated"),
            "total_cohort": 1396,
            "evaluated_count": overall.get("n", 1396),
            "is_full": is_full_eval,
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

        models_1396.append(model_obj)
        categories_1396[key] = cat_dict

    models_1396.sort(key=lambda m: (m["is_full"], m["mrr"]), reverse=True)

    return {
        "metadata": {
            "title": "RareDx Bench: Variant Ranking",
            "subtitle": "Gene prioritization benchmark across rare disease patient cases",
            "total_cohort": 311,
            "default_cohort": "challenge_311",
            "categories": CATEGORY_ORDER,
            "category_labels": CATEGORY_LABELS,
            "category_counts": CATEGORY_COUNTS_311,
            "category_counts_full": CATEGORY_COUNTS_FULL,
            "category_counts_311": CATEGORY_COUNTS_311
        },
        "cohorts": {
            "challenge_311": {
                "title": "311 Challenge Benchmark (Hard & Discriminative Cases)",
                "subtitle": "Direct head-to-head evaluation across all 12 frontier LLMs and classical phenotype tools on the 311 hardest discriminative cases",
                "total_cohort": 311,
                "category_counts": CATEGORY_COUNTS_311,
                "models": models_311,
                "categories_data": categories_311
            },
            "full_1396": {
                "title": "Full Cohort (1,396 Cases)",
                "subtitle": "Gene prioritization benchmark across all 1,396 simulated rare disease patient cases",
                "total_cohort": 1396,
                "category_counts": CATEGORY_COUNTS_FULL,
                "models": models_1396,
                "categories_data": categories_1396
            }
        },
        # Default payload exposes the 311 challenge cohort where all 12 models participate:
        "models": models_311,
        "categories_data": categories_311
    }
