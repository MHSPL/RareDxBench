#!/usr/bin/env python3
"""
Dashboard Data Builder for RareDx Bench (variant classification).

Scans all model results in this repository, computes comprehensive
metrics, confusion matrices, disease category breakdowns, safety audits,
ACMG criteria frequencies, and pairwise concordance, and outputs a single
JSON dataset consumed by dashboard/generate_html.py.

Dynamic & Extensible: Re-running this script or adding any new result file
instantly refreshes the dashboard.

Modules: loaders.py (answers, registry, predictions), scoring.py (per-model
metrics, concordance), variant_table.py, archive.py (archived model carry-over).
"""

import json
import sys
from datetime import datetime
from pathlib import Path

# Add repo root to path
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from config import CLASSIFICATION_LABELS, DIRECTION_LABELS, CLASSIFICATION_ANSWERS
from scripts.categories import CATEGORY_ORDER
from dashboard.loaders import (  # noqa: F401  (re-exported)
    get_vcep_category, load_unified_data, discover_model_files,
    STRENGTH_WORDS, with_strength, criterion_points, load_model_predictions,
)
from dashboard.scoring import macro_f1_directional, score_model, concordance  # noqa: F401
from dashboard.variant_table import VARIANT_COLUMNS, NO_CALL, build_variant_table  # noqa: F401
from dashboard.archive import load_previous_dataset, carry_over_archived_models  # noqa: F401
from dashboard.ranking_scoring import build_ranking_data


def build_dashboard_dataset():
    print(f"[{datetime.now().strftime('%T')}] Initializing Dashboard Data Builder...")
    unified_path = CLASSIFICATION_ANSWERS
    print(f"Loading Ground Truth & Baselines from {unified_path}...")
    unified_dict = load_unified_data(unified_path)
    total_cohort = len(unified_dict)
    print(f"Loaded {total_cohort} ClinGen expert curated variants.")

    models_info, missing = discover_model_files(REPO_ROOT)
    print(f"Discovered {len(models_info)} models and baselines:")
    for k, m in models_info.items():
        print(f"  • [{m['family']}] {m['name']} ({m['format']})")

    # Load predictions for all models
    model_predictions = {}
    for m_key, m_info in models_info.items():
        print(f"Loading predictions for {m_key}...")
        preds = load_model_predictions(m_info, unified_dict)
        model_predictions[m_key] = preds
        print(f"  -> {len(preds)} valid predictions loaded.")

    # -------------------------------------------------------------------------
    # COMPUTE COMPREHENSIVE METRICS FOR EACH MODEL
    # -------------------------------------------------------------------------
    models_summary = []
    models_confusion = {}
    models_categories = {}
    models_criteria = {}
    models_safety = {}
    models_cross = {}

    for m_key, m_info in models_info.items():
        scored = score_model(m_key, m_info, model_predictions[m_key], unified_dict, total_cohort)
        if scored is None:
            continue
        models_summary.append(scored["summary"])
        models_confusion[m_key] = scored["confusion"]
        models_categories[m_key] = scored["categories"]
        models_cross[m_key] = scored["cross"]
        models_criteria[m_key] = scored["criteria"]
        models_safety[m_key] = scored["safety"]

    # -------------------------------------------------------------------------
    # PAIRWISE MODEL CONCORDANCE
    # -------------------------------------------------------------------------
    model_keys = [m["key"] for m in models_summary]
    concordance_matrix = concordance(model_predictions, model_keys)

    # -------------------------------------------------------------------------
    # EVERY BENCHMARK VARIANT WITH EACH MODEL'S CALL (the page's CSV download)
    # -------------------------------------------------------------------------
    variant_table = build_variant_table(unified_dict, model_predictions, model_keys)

    # -------------------------------------------------------------------------
    # VARIANT RANKING BENCHMARK
    # -------------------------------------------------------------------------
    ranking_data = build_ranking_data(REPO_ROOT)
    print(f"Loaded variant ranking benchmark: {len(ranking_data['models'])} models.")

    # -------------------------------------------------------------------------
    # FINAL ASSEMBLED DASHBOARD PAYLOAD
    # -------------------------------------------------------------------------
    payload = {
        "metadata": {
            "title": "RareDx Bench",
            "subtitle": "An open rare disease diagnostics benchmark for any AI model or classical tool, scored against ClinGen expert classifications",
            "evaluation_date": datetime.now().strftime("%B %d, %Y"),
            "total_variants": total_cohort,
            "categories": CATEGORY_ORDER,
            "classification_labels": CLASSIFICATION_LABELS,
            "direction_labels": DIRECTION_LABELS,
        },
        "models": models_summary,
        "confusion_matrices": models_confusion,
        "categories_data": models_categories,
        "criteria_data": models_criteria,
        "safety_data": models_safety,
        "cross_direction": models_cross,
        "concordance": concordance_matrix,
        "variant_table": variant_table,
        "ranking_data": ranking_data,
    }

    carry_over_archived_models(payload, missing)

    out_file = REPO_ROOT / "dashboard" / "data" / "benchmark_data.json"
    out_file.parent.mkdir(parents=True, exist_ok=True)
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

    print(f"[{datetime.now().strftime('%T')}] Wrote dashboard data to {out_file}")
    print(f"Data file size: {out_file.stat().st_size / 1024:.1f} KB")

    return payload


if __name__ == "__main__":
    build_dashboard_dataset()
