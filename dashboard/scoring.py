"""
Per-model scoring for the dashboard build: summary metrics, confusion
matrices, disease category breakdowns, cross-direction errors, safety
samples, ACMG criteria frequencies, and pairwise model concordance.
"""

from collections import Counter

from config import CLASSIFICATION_LABELS, DIRECTION_LABELS
from scripts.categories import CATEGORY_ORDER
from scripts.metrics import get_direction, calculate_class_metrics, calculate_direction_metrics, calculate_macro_f1
from dashboard.loaders import criterion_points

CLASSIFICATION_LABELS_4 = [
    "Pathogenic",
    "Likely Pathogenic",
    "Likely Benign",
    "Benign",
]
DIRECTION_LABELS_2 = ["pathogenic", "benign"]

CLASSIFICATION_LABELS_COLS = [
    "Pathogenic",
    "Likely Pathogenic",
    "Likely Benign",
    "Benign",
    "Uncertain Significance",
]
DIRECTION_LABELS_COLS = ["pathogenic", "benign", "vus"]

ALL_CRITERIA = sorted({
    "PVS1", "PS1", "PS2", "PS3", "PS4",
    "PM1", "PM2", "PM3", "PM4", "PM5", "PM6",
    "PP1", "PP2", "PP3", "PP4", "PP5",
    "BA1", "BS1", "BS2", "BS3", "BS4",
    "BP1", "BP2", "BP3", "BP4", "BP5", "BP7"
})


def macro_f1_directional(gt_2: list[str], pred_2: list[str]) -> float:
    """Macro F1 over the two directions (pathogenic, benign)."""
    m = calculate_direction_metrics(gt_2, pred_2, DIRECTION_LABELS_2)
    f1s = [m[d.lower()]["f1"] for d in DIRECTION_LABELS_2 if m.get(d.lower(), {}).get("support", 0) > 0]
    return sum(f1s) / len(f1s) if f1s else 0.0


def confusion_5(gt_4, pred_5):
    # 4 expert rows (P, LP, LB, B), 5 model columns (P, LP, LB, B, VUS non-calls)
    cm = [[0] * len(CLASSIFICATION_LABELS_COLS) for _ in range(len(CLASSIFICATION_LABELS_4))]
    for g, p in zip(gt_4, pred_5):
        if g in CLASSIFICATION_LABELS_4 and p in CLASSIFICATION_LABELS_COLS:
            cm[CLASSIFICATION_LABELS_4.index(g)][CLASSIFICATION_LABELS_COLS.index(p)] += 1
    return cm


def confusion_3(gt_2, pred_2):
    # 2 expert rows (pathogenic, benign), 3 model columns (pathogenic, benign, vus non-calls)
    cm = [[0] * len(DIRECTION_LABELS_COLS) for _ in range(len(DIRECTION_LABELS_2))]
    d_lower_cols = [d.lower() for d in DIRECTION_LABELS_COLS]
    d_lower_rows = [d.lower() for d in DIRECTION_LABELS_2]
    for g, p in zip(gt_2, pred_2):
        g_low = (g or "").lower()
        p_low = (p or "").lower()
        if g_low in d_lower_rows and p_low in d_lower_cols:
            cm[d_lower_rows.index(g_low)][d_lower_cols.index(p_low)] += 1
    return cm


def critical_errors(evaluated_ids, unified_dict, preds):
    """
    Safety / critical clinical errors:
    false pathogenic (expert B/LB, model P/LP) and false benign (expert P/LP, model B/LB).
    """
    false_pathogenic_ids, false_benign_ids = [], []
    for vid in evaluated_ids:
        g_dir = (unified_dict[vid]["ground_truth_direction"] or "").lower()
        p_dir = (get_direction(preds[vid]["prediction"]) or "").lower()
        if g_dir == "benign" and p_dir == "pathogenic":
            false_pathogenic_ids.append(vid)
        elif g_dir == "pathogenic" and p_dir == "benign":
            false_benign_ids.append(vid)
    return false_pathogenic_ids, false_benign_ids


def _pct(metrics: dict, key: str) -> float:
    return round(metrics.get(key, 0) * 100, 2)


def model_summary(m_key, m_info, gt_5, pred_5, gt_3, pred_3, n_eval, total_cohort, false_pathogenic, false_benign, avg_time):
    exact_matches = sum(1 for g, p in zip(gt_5, pred_5) if g == p and g != "")
    directional_matches = sum(1 for g, p in zip(gt_3, pred_3) if g == p and g in DIRECTION_LABELS_2)

    per_class_5 = calculate_class_metrics(gt_5, pred_5, CLASSIFICATION_LABELS_4)
    per_class_3 = calculate_direction_metrics(gt_3, pred_3, DIRECTION_LABELS_2)
    valid_3_f1s = [per_class_3[d.lower()]["f1"] for d in DIRECTION_LABELS_2 if per_class_3.get(d.lower(), {}).get("support", 0) > 0]
    total_critical = false_pathogenic + false_benign

    macro_4 = round(calculate_macro_f1(per_class_5) * 100, 2)
    macro_2 = round((sum(valid_3_f1s) / len(valid_3_f1s) * 100) if valid_3_f1s else 0, 2)

    return {
        "key": m_key,
        "name": m_info["name"],
        "short_name": m_info["short_name"],
        "family": m_info["family"],
        "is_anthropic": m_info["is_anthropic"],
        "type": m_info["type"],
        "color": m_info["color"],
        "color_dark": m_info.get("color_dark", m_info["color"]),
        "description": m_info["description"],
        "section": m_info["section"],
        "evaluated_count": n_eval,
        "total_cohort": total_cohort,
        "coverage_pct": round(n_eval / total_cohort * 100, 1),
        "exact_accuracy": round(exact_matches / n_eval * 100, 2),
        "directional_accuracy": round(directional_matches / n_eval * 100, 2),
        "macro_f1_5": macro_4,
        "macro_f1_4": macro_4,
        "macro_f1_3": macro_2,
        "macro_f1_2": macro_2,
        "critical_error_count": total_critical,
        "critical_error_rate": round(total_critical / n_eval * 100, 2),
        "false_pathogenic_count": false_pathogenic,
        "false_benign_count": false_benign,
        "avg_latency_sec": avg_time,
        "per_class_5": {
            c: {
                "precision": _pct(per_class_5[c], "precision"),
                "recall": _pct(per_class_5[c], "recall"),
                "f1": _pct(per_class_5[c], "f1"),
                "support": per_class_5[c]["support"],
                "tp": per_class_5[c]["true_positives"],
                "fp": per_class_5[c]["false_positives"],
                "fn": per_class_5[c]["false_negatives"],
            }
            for c in CLASSIFICATION_LABELS_4
        },
        "per_class_3": {
            d: {
                "precision": _pct(per_class_3.get(d.lower(), {}), "precision"),
                "recall": _pct(per_class_3.get(d.lower(), {}), "recall"),
                "f1": _pct(per_class_3.get(d.lower(), {}), "f1"),
                "support": per_class_3.get(d.lower(), {}).get("support", 0),
            }
            for d in DIRECTION_LABELS_2
        },
    }


def category_breakdown(evaluated_ids, unified_dict, preds) -> dict:
    cat_metrics = {}
    for cat in CATEGORY_ORDER:
        c_vids = [vid for vid in evaluated_ids if unified_dict[vid]["category"] == cat]
        if not c_vids:
            continue
        c_gt = [unified_dict[vid]["ground_truth"] for vid in c_vids]
        c_pred = [preds[vid]["prediction"] for vid in c_vids]
        c_gt3 = [unified_dict[vid]["ground_truth_direction"] for vid in c_vids]
        c_pred3 = [get_direction(p) for p in c_pred]

        c_exact = sum(1 for g, p in zip(c_gt, c_pred) if g == p)
        c_dir = sum(1 for g, p in zip(c_gt3, c_pred3) if g == p and g in DIRECTION_LABELS_2)
        c_m = calculate_class_metrics(c_gt, c_pred, CLASSIFICATION_LABELS_4)

        cat_metrics[cat] = {
            "count": len(c_vids),
            "exact_correct": c_exact,
            "directional_correct": c_dir,
            "matrix_5": confusion_5(c_gt, c_pred),
            "exact_accuracy": round(c_exact / len(c_vids) * 100, 2),
            "directional_accuracy": round(c_dir / len(c_vids) * 100, 2),
            "macro_f1": round(calculate_macro_f1(c_m) * 100, 2),
            "macro_f1_3": round(macro_f1_directional(c_gt3, c_pred3) * 100, 2),
            "plp_f1": round((c_m["Pathogenic"]["f1"] + c_m["Likely Pathogenic"]["f1"]) / 2 * 100, 2),
            "blb_f1": round((c_m["Benign"]["f1"] + c_m["Likely Benign"]["f1"]) / 2 * 100, 2),
        }
    return cat_metrics


def cross_record(vid, direction, unified_dict, preds) -> dict:
    """One cross-direction error, with expert and model evidence."""
    u = unified_dict[vid]
    p = preds[vid]
    return {
        "id": vid,
        "direction": direction,
        "gene": u["gene"],
        "variant": u["variation"],
        "disease": u["disease"],
        "category": u["category"],
        "expert_panel": u["expert_panel"],
        "ground_truth": u["ground_truth"],
        "expert_criteria": u["criteria_met_raw"],
        "expert_points": sum(criterion_points(c) for c in u["criteria_met_raw"]),
        "predicted": p["prediction"],
        "model_criteria": p.get("criteria_raw") or p.get("criteria") or [],
        "model_points": p.get("score"),
        "confidence": p.get("confidence") or "",
    }


def safety_sample(vid, unified_dict, preds) -> dict:
    return {
        "id": vid,
        "gene": unified_dict[vid]["gene"],
        "variant": unified_dict[vid]["variation"],
        "disease": unified_dict[vid]["disease"],
        "ground_truth": unified_dict[vid]["ground_truth"],
        "predicted": preds[vid]["prediction"],
        "criteria": preds[vid]["criteria"][:5],
    }


def score_model(m_key, m_info, preds, unified_dict, total_cohort):
    """Every dashboard section for one model, or None when it has no evaluated variants."""
    if not preds:
        return None
    evaluated_ids = [vid for vid in unified_dict if vid in preds]
    n_eval = len(evaluated_ids)
    if n_eval == 0:
        return None

    gt_5 = [unified_dict[vid]["ground_truth"] for vid in evaluated_ids]
    pred_5 = [preds[vid]["prediction"] for vid in evaluated_ids]
    gt_3 = [unified_dict[vid]["ground_truth_direction"] for vid in evaluated_ids]
    pred_3 = [get_direction(p) for p in pred_5]

    false_pathogenic_ids, false_benign_ids = critical_errors(evaluated_ids, unified_dict, preds)

    crit_counter = Counter()
    for vid in evaluated_ids:
        for c in preds[vid]["criteria"]:
            if c:
                crit_counter[c] += 1

    times = [preds[vid]["execution_time_ms"] for vid in evaluated_ids if preds[vid].get("execution_time_ms", 0) > 0]
    avg_time = round(sum(times) / len(times) / 1000, 1) if times else 0.0

    return {
        "summary": model_summary(m_key, m_info, gt_5, pred_5, gt_3, pred_3, n_eval, total_cohort,
                                 len(false_pathogenic_ids), len(false_benign_ids), avg_time),
        "confusion": {
            "matrix_5": confusion_5(gt_5, pred_5),
            "matrix_3": confusion_3(gt_3, pred_3),
            "labels_5": CLASSIFICATION_LABELS_4,
            "labels_3": DIRECTION_LABELS_2,
            "col_labels_5": CLASSIFICATION_LABELS_COLS,
            "col_labels_3": DIRECTION_LABELS_COLS,
        },
        "categories": category_breakdown(evaluated_ids, unified_dict, preds),
        "cross": [cross_record(v, "B→P", unified_dict, preds) for v in false_pathogenic_ids]
                 + [cross_record(v, "P→B", unified_dict, preds) for v in false_benign_ids],
        "criteria": {c: crit_counter.get(c, 0) for c in ALL_CRITERIA},
        "safety": {
            "false_pathogenic_count": len(false_pathogenic_ids),
            "false_benign_count": len(false_benign_ids),
            "top_false_pathogenic": [safety_sample(v, unified_dict, preds) for v in false_pathogenic_ids[:10]],
            "top_false_benign": [safety_sample(v, unified_dict, preds) for v in false_benign_ids[:10]],
        },
    }


def concordance(model_predictions: dict, model_keys: list[str]) -> dict:
    """Pairwise exact and directional agreement over the variants both models called."""
    matrix = {k1: {} for k1 in model_keys}
    for k1 in model_keys:
        for k2 in model_keys:
            if k1 == k2:
                matrix[k1][k2] = {"exact_pct": 100.0, "dir_pct": 100.0, "overlap": len(model_predictions[k1])}
                continue
            p1, p2 = model_predictions[k1], model_predictions[k2]
            common = set(p1.keys()) & set(p2.keys())
            if not common:
                matrix[k1][k2] = {"exact_pct": 0.0, "dir_pct": 0.0, "overlap": 0}
                continue
            exact_agrees = sum(1 for vid in common if p1[vid]["prediction"] == p2[vid]["prediction"])
            dir_agrees = sum(1 for vid in common if get_direction(p1[vid]["prediction"]) == get_direction(p2[vid]["prediction"]))
            matrix[k1][k2] = {
                "exact_pct": round(exact_agrees / len(common) * 100, 1),
                "dir_pct": round(dir_agrees / len(common) * 100, 1),
                "overlap": len(common),
            }
    return matrix
