#!/usr/bin/env python3
"""
Classification of the 497 benchmark variants with GPT-6.2 Sol
(openai/gpt-6.2-sol). The run logic lives in scripts/overnight_runner.py.

Usage:
    python scripts/run_sol62_overnight.py [--input FILE] [--concurrency 5] [--max-passes 4]
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import CLASSIFICATION_RESULTS_DIR
from scripts.overnight_runner import main

MODEL_NAME = "openai/gpt-6.2-sol"
OUTPUT_FILE = CLASSIFICATION_RESULTS_DIR / "Results_GPT_6_2_Sol_Analysis" / "sol62_full.jsonl"

if __name__ == "__main__":
    main(MODEL_NAME, OUTPUT_FILE, "GPT-6.2 Sol classifier", default_concurrency=4)
