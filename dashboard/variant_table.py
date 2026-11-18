"""
The variant table embedded in the dashboard (the page's CSV download): every
benchmark variant with the expert classification and each model's call.
"""

from config import CLASSIFICATION_LABELS
from dashboard.loaders import criterion_points


VARIANT_COLUMNS = ["id", "gene", "variation", "variant_key", "disease", "category", "expert_panel",
                   "ground_truth", "expert_criteria", "expert_points"]
NO_CALL = "-"


def build_variant_table(unified_dict: dict, model_predictions: dict, model_keys: list[str]) -> dict:
    """
    Every benchmark variant with the expert classification and each model's call,
    kept compact for embedding in the page: one row per variant (VARIANT_COLUMNS),
    and per model a string with one character per row, the index of the call in
    CLASSIFICATION_LABELS or NO_CALL.
    """
    ids = sorted(unified_dict, key=lambda v: (unified_dict[v]["gene"], unified_dict[v]["variation"], v))
    rows = []
    for vid in ids:
        u = unified_dict[vid]
        rows.append([vid, u["gene"], u["variation"], u["variant_key"], u["disease"], u["category"], u["expert_panel"],
                     u["ground_truth"], " ".join(u["criteria_met_raw"]),
                     sum(criterion_points(c) for c in u["criteria_met_raw"])])
    labels = ["Pathogenic", "Likely Pathogenic", "Likely Benign", "Benign", "Uncertain Significance"]
    code = {label: str(i) for i, label in enumerate(labels)}
    calls = {}
    for k in model_keys:
        preds = model_predictions[k]
        calls[k] = "".join(code.get(preds[vid]["prediction"], NO_CALL) if vid in preds else NO_CALL for vid in ids)
    return {"columns": VARIANT_COLUMNS, "labels": labels, "rows": rows, "calls": calls}
