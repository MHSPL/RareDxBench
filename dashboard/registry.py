"""
Model registry for the benchmark dashboard (dashboard/models.tsv).

One row per model; adding a model to the dashboard is one line here plus its
results file. Columns (tab-separated):

    key         stable identifier, used in links and colours
    name        display name
    provider    Anthropic, OpenAI, Google, Rule-based or Other
    method      how the calls were produced, e.g. "AIVA agent"
    path        results file relative to the repo root; for the baselines,
                data:bias_classification or data:intervar_classification
    format      jsonl (AIVA output), json_array (Claude Code output),
                auto (by file extension) or unified (baseline field)
    color       optional hex colour; empty takes the next unused PALETTE colour
    color_dark  optional colour for the dark theme; empty takes the PALETTE
                pair of `color`, else `color` itself
    status      active, or removed to hide the model
    section     leaderboard section: aiva (AIVA agent runs), agent (Claude
                app runs such as Claude Code) or automated
                (rule-based pipelines); empty derives it from provider/method
"""

from pathlib import Path

REGISTRY_FILE = Path(__file__).resolve().parent / "models.tsv"
COLUMNS = ["key", "name", "provider", "method", "path", "format", "color", "color_dark", "status", "section"]
PROVIDERS = ["Anthropic", "OpenAI", "Google", "Rule-based", "Other"]
SECTIONS = ["aiva", "agent", "automated"]


def section_of(row: dict) -> str:
    """Explicit `section`, else automated for rule-based, aiva for AIVA runs, agent otherwise."""
    if row.get("section") in SECTIONS:
        return row["section"]
    if row.get("provider") == "Rule-based" or "rules" in row.get("method", "").lower():
        return "automated"
    if "aiva" in f"{row.get('method', '')} {row.get('name', '')}".lower():
        return "aiva"
    return "agent"

# Model colours, drawn from the Wes Anderson palettes (wesanderson R package).
# Each entry is (light theme, dark theme, source palette). The dark-theme
# colour keeps the hue and is lighter only where the light one falls below
# 3:1 contrast on the dark surface (#0f2c21). The first eleven are assigned
# to the current models in models.tsv; models added later take the next
# unused entry. Every chart, table,
# legend and swatch reads the model's colour from here, so a model keeps one
# colour across the whole dashboard.
PALETTE = [
    ("#BD3027", "#D04438", "The French Dispatch"),
    ("#FD6467", "#FD6467", "The Grand Budapest Hotel 1"),
    ("#046C9A", "#237CAB", "Darjeeling Limited 2"),
    ("#7FC0C6", "#7FC0C6", "The French Dispatch"),
    ("#6C8645", "#6C8645", "Asteroid City 1"),
    ("#D8A499", "#D8A499", "The Grand Budapest Hotel 2"),
    ("#E1AF00", "#E1AF00", "The Life Aquatic (Zissou 1)"),
    ("#C27D38", "#C27D38", "Moonrise Kingdom 2"),
    ("#0A9F9D", "#0A9F9D", "Asteroid City 1"),
    ("#79402E", "#A26552", "Isle of Dogs 1"),
    ("#9986A5", "#9986A5", "Isle of Dogs 1"),
    # Reserve for models added later
    ("#5785C1", "#5785C1", "Asteroid City 3"),
    ("#F98400", "#F98400", "Darjeeling Limited 1"),
    ("#9A8822", "#9A8822", "The Royal Tenenbaums 2"),
    ("#E6A0C4", "#E6A0C4", "The Grand Budapest Hotel 2"),
    ("#3B9AB2", "#3B9AB2", "The Life Aquatic (Zissou 1)"),
    ("#CB7A5C", "#CB7A5C", "Asteroid City 3"),
]
DARK_STEP = {light.lower(): dark for light, dark, _ in PALETTE}


def read_registry(path: Path = REGISTRY_FILE) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        parts = line.split("\t") + [""] * len(COLUMNS)
        row = dict(zip(COLUMNS, (p.strip() for p in parts[: len(COLUMNS)])))
        row["status"] = row["status"] or "active"
        row["provider"] = row["provider"] if row["provider"] in PROVIDERS else "Other"
        row["section"] = section_of(row)
        rows.append(row)
    return rows


def assign_colors(rows: list[dict]) -> None:
    """Fill empty colours with the next unused PALETTE entry, and dark-theme steps from it."""
    used = {r["color"].lower() for r in rows if r["color"]}
    for r in rows:
        if not r["color"]:
            free = [light for light, _, _ in PALETTE if light.lower() not in used]
            r["color"] = free[0] if free else PALETTE[len(used) % len(PALETTE)][0]
            used.add(r["color"].lower())
        if not r["color_dark"]:
            r["color_dark"] = DARK_STEP.get(r["color"].lower(), "")
