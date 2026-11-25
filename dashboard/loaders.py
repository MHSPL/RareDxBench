"""
Loaders for the dashboard build: the benchmark answers (ground truth plus the
BIAS-2015 and InterVar baselines), the model registry, and each model's
predictions. Also ACMG criterion strength and points helpers.
"""

import json
from pathlib import Path

from config import CLASSIFICATION_RESULTS_DIR
from scripts.categories import VCEP_TO_CATEGORY
from scripts.metrics import normalize_classification, get_direction, normalize_criterion
from dashboard.registry import read_registry, assign_colors


def get_vcep_category(panel: str) -> str:
    if not panel:
        return "Other"
    return VCEP_TO_CATEGORY.get(panel.strip(), "Other")


def load_unified_data(path: Path) -> dict:
    """Load the benchmark dataset: variant id -> variant dict."""
    variants = {}
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                item = json.loads(line)
                vid = item["id"]
                gt_norm = normalize_classification(item.get("classification", ""))
                panel = item.get("expert_panel", "")
                cat = get_vcep_category(panel)
                variants[vid] = {
                    "id": vid,
                    "gene": item.get("gene_symbol", ""),
                    "variation": item.get("variation", ""),
                    "variant_key": item.get("variant_key", ""),
                    "disease": item.get("disease", ""),
                    "expert_panel": panel,
                    "category": cat,
                    "ground_truth": gt_norm,
                    "ground_truth_direction": get_direction(gt_norm),
                    "criteria_met": [normalize_criterion(c) for c in (item.get("criteria_met") or [])],
                    "criteria_met_raw": list(item.get("criteria_met") or []),
                    "intervar_classification": normalize_classification(item.get("intervar_classification", "")),
                    "intervar_criteria": [normalize_criterion(c) for c in (item.get("intervar_criteria") or [])],
                    "intervar_criteria_raw": list(item.get("intervar_criteria") or []),
                    "bias_classification": normalize_classification(item.get("bias_classification", "")),
                    "bias_criteria": [normalize_criterion(c) for c in (item.get("bias_criteria") or [])],
                    "bias_criteria_raw": list(item.get("bias_criteria") or []),
                }
    return variants


def discover_model_files(repo_root: Path) -> tuple[dict[str, dict], set[str]]:
    """
    Read dashboard/models.tsv. Returns (active models with results present,
    keys of active models whose results file is missing). Rows whose status is
    not "active" are left out.
    """
    rows = read_registry()
    assign_colors(rows)
    discovered, missing = {}, set()
    for row in rows:
        key = row["key"]
        if row["status"] != "active":
            continue
        fmt = row["format"] or "auto"
        if fmt == "unified" or row["path"].startswith("data:"):
            fmt, path = "unified_field", "unified:" + row["path"].split(":", 1)[1]
        else:
            file = repo_root / row["path"]
            if not file.exists():
                missing.add(key)
                print(f"  ! {row['name']}: results file not found ({row['path']})")
                continue
            if fmt == "auto":
                fmt = "json_array" if file.suffix == ".json" else "jsonl"
            path = str(file)
        discovered[key] = {
            "name": row["name"],
            "short_name": row["name"],
            "family": row["provider"],
            "is_anthropic": row["provider"] == "Anthropic",
            "type": row["method"] or "Unspecified",
            "path": path,
            "format": fmt,
            "color": row["color"],
            "color_dark": row["color_dark"] or row["color"],
            "description": row["method"],
            "section": row["section"],
        }

    registered = {(repo_root / r["path"]).parent.resolve() for r in rows if not r["path"].startswith("data:")}
    unregistered = sorted(d.name for d in CLASSIFICATION_RESULTS_DIR.iterdir() if d.is_dir() and d.resolve() not in registered and any(d.glob("*.jsonl"))) if CLASSIFICATION_RESULTS_DIR.is_dir() else []
    if unregistered:
        print("  Result folders not in dashboard/models.tsv (not shown): " + ", ".join(unregistered))
    return discovered, missing


STRENGTH_WORDS = ("supporting", "moderate", "strong", "very strong", "very_strong", "stand-alone", "standalone")


def with_strength(code: str, strength: str) -> str:
    s = strength.strip().lower()
    if "_" in code or s not in STRENGTH_WORDS:
        return code
    return f"{code}_{strength.strip().replace(' ', '_')}"


def criterion_points(criterion: str) -> int:
    """
    Points for one ACMG criterion on the ClinGen/Tavtigian (2020) scale:
    supporting 1, moderate 2, strong 4, very strong 8 (BA1 stand-alone 8),
    negative for benign evidence. An explicit strength modifier
    (e.g. PM2_Supporting) overrides the criterion's default strength.
    """
    low = criterion.strip().lower().replace(" ", "_").replace("-", "_")
    base = normalize_criterion(criterion).upper()
    if not base:
        return 0
    if "very" in low or "stand" in low:
        pts = 8
    elif "_strong" in low:
        pts = 4
    elif "_moderate" in low:
        pts = 2
    elif "_supporting" in low:
        pts = 1
    elif base.startswith(("PVS", "BA")):
        pts = 8
    elif base.startswith(("PS", "BS")):
        pts = 4
    elif base.startswith("PM"):
        pts = 2
    else:
        pts = 1
    return -pts if base.startswith("B") else pts


def load_model_predictions(model_info: dict, unified_dict: dict) -> dict[str, dict]:
    """
    Load variant_id -> {prediction, criteria, time_ms, raw} for a given model.
    """
    fmt = model_info["format"]
    path_str = model_info["path"]
    preds = {}

    if fmt == "unified_field":
        field = path_str.split(":")[1]
        crit_field = "bias_criteria" if "bias" in field else "intervar_criteria"
        for vid, v in unified_dict.items():
            p_raw = v.get(field, "")
            preds[vid] = {
                "prediction": p_raw,
                "criteria": v.get(crit_field, []),
                "criteria_raw": v.get(crit_field + "_raw", []),
                "score": None,
                "execution_time_ms": 0,
                "error": None,
            }
        return preds

    if fmt == "json_array":
        with open(path_str, "r", encoding="utf-8") as f:
            data = json.load(f)
        for item in data:
            vid = item.get("id")
            if not vid:
                continue
            met = item.get("criteria", {}).get("met", {}) or {}
            crit = list(met.keys())
            preds[vid] = {
                # Strength is the prefix of each criterion's note, e.g. "Supporting: ..."
                "criteria_raw": [with_strength(k, str(v).split(":")[0]) for k, v in met.items()],
                "score": item.get("acmg_points"),
                "prediction": normalize_classification(item.get("classification", "")),
                "criteria": [normalize_criterion(c) for c in crit],
                "execution_time_ms": item.get("execution_time_ms", 0),
                "error": None,
                "confidence": item.get("confidence", ""),
                "rationale": item.get("disease_context", ""),
            }
        return preds

    if fmt == "jsonl":
        with open(path_str, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                vid = item.get("variant_id") or item.get("id")
                if not vid:
                    continue
                cls_raw = item.get("aiva_classification") or item.get("classification", "")
                err = item.get("error")
                if err or not cls_raw or cls_raw == "Not classified":
                    if vid not in preds:
                        preds[vid] = {
                            "prediction": "Not classified",
                            "criteria": [],
                            "execution_time_ms": item.get("execution_time_ms", 0),
                            "error": err,
                            "confidence": "",
                            "evidence": {},
                            "criteria_raw": [],
                            "score": None,
                        }
                    continue
                preds[vid] = {
                    "prediction": normalize_classification(cls_raw),
                    "criteria": [normalize_criterion(c) for c in (item.get("aiva_criteria") or item.get("criteria") or [])],
                    "execution_time_ms": item.get("execution_time_ms", 0),
                    "error": err,
                    "confidence": item.get("aiva_confidence", ""),
                    "evidence": item.get("aiva_evidence", {}),
                    "criteria_raw": list(item.get("aiva_criteria") or item.get("criteria") or []),
                    "score": item.get("aiva_acmg_score"),
                }
        return preds

    return preds
