#!/usr/bin/env python3
"""
Monitor ranking runs for Gemini 4 Neon, GPT-6.2 Sol, and GPT-6.2 Astra.
Checks progress, logs status, rebuilds dashboard, and pushes when ready.
"""

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

MODELS = [
    {
        "name": "Gemini 4 Neon",
        "key": "neon",
        "file": REPO_ROOT / "results" / "ranking" / "Results_Gemini_4_Neon" / "neon_311_predictions.jsonl",
        "log": REPO_ROOT / "results" / "ranking" / "Results_Gemini_4_Neon" / "neon_311_run.log",
        "screen": "neon_rank",
    },
    {
        "name": "GPT-6.2 Sol",
        "key": "sol62",
        "file": REPO_ROOT / "results" / "ranking" / "Results_GPT_6_2_Sol" / "sol62_311_predictions.jsonl",
        "log": REPO_ROOT / "results" / "ranking" / "Results_GPT_6_2_Sol" / "sol62_311_run.log",
        "screen": "sol62_rank",
    },
    {
        "name": "GPT-6.2 Astra",
        "key": "astra62",
        "file": REPO_ROOT / "results" / "ranking" / "Results_GPT_6_2_Astra" / "astra62_311_predictions.jsonl",
        "log": REPO_ROOT / "results" / "ranking" / "Results_GPT_6_2_Astra" / "astra62_311_run.log",
        "screen": "astra62_rank",
    },
]

TOTAL_CASES = 311


def count_predictions(path: Path) -> tuple[int, int]:
    """Return (valid_predictions, total_lines)."""
    if not path.exists():
        return 0, 0
    valid = 0
    total = 0
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            total += 1
            try:
                rec = json.loads(line)
                if rec.get("predicted"):
                    valid += 1
            except Exception:
                pass
    return valid, total


def check_screen_running(name: str) -> bool:
    try:
        res = subprocess.run(["screen", "-ls"], capture_output=True, text=True)
        return name in res.stdout
    except Exception:
        return False


def main():
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] === RANKING 311 BENCHMARK MONITOR CHECK ===")

    all_done = True
    any_updated = False

    for m in MODELS:
        valid, total = count_predictions(m["file"])
        running = check_screen_running(m["screen"])
        status = "RUNNING" if running else ("COMPLETE" if total >= TOTAL_CASES else "STOPPED/IDLE")
        pct = (total / TOTAL_CASES) * 100 if TOTAL_CASES > 0 else 0
        print(f"  • {m['name']:15s}: {total}/{TOTAL_CASES} cases ({pct:.1f}%) | {valid} resolved | Status: {status} (Screen: {m['screen']})")

        if total < TOTAL_CASES:
            all_done = False
        if total > 0:
            any_updated = True

    if any_updated:
        print("\nRebuilding dashboard with latest available predictions...")
        up_script = REPO_ROOT / "scripts" / "update_dashboard.py"
        subprocess.run([sys.executable, str(up_script)], cwd=str(REPO_ROOT))

        # Check if there are changes to commit and push
        status_check = subprocess.run(["git", "status", "--porcelain", "dashboard/", "results/ranking/"], cwd=str(REPO_ROOT), capture_output=True, text=True)
        if status_check.stdout.strip():
            print("\nNew predictions or dashboard updates detected. Committing and pushing progress to origin/dashboard...")
            subprocess.run(["git", "add", "dashboard/", "results/ranking/"], cwd=str(REPO_ROOT))
            neon_v, neon_t = count_predictions(MODELS[0]["file"])
            sol_v, sol_t = count_predictions(MODELS[1]["file"])
            astra_v, astra_t = count_predictions(MODELS[2]["file"])
            msg = f"feat(ranking): progress update (Neon: {neon_t}/311, Sol 6.2: {sol_t}/311, Astra 6.2: {astra_t}/311)"
            subprocess.run(["git", "commit", "-m", msg], cwd=str(REPO_ROOT))
            subprocess.run(["git", "push", "origin", "dashboard"], cwd=str(REPO_ROOT))
            print("Pushed updates cleanly to origin/dashboard (PR #2)!")

    if all_done:
        print("\nAll 3 models have finished 311 cases!")

    print("==================================================")


if __name__ == "__main__":
    main()
