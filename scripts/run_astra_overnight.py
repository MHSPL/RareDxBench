#!/usr/bin/env python3
"""
Overnight classification of the 2,894 benchmark variants with GPT-6 Astra
(openai/gpt-6-astra). The run logic lives in scripts/overnight_runner.py.

Usage:
    python scripts/run_astra_overnight.py [--input FILE] [--concurrency 7] [--max-passes 4]
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import CLASSIFICATION_RESULTS_DIR
from scripts.overnight_runner import main

MODEL_NAME = "openai/gpt-6-astra"
OUTPUT_FILE = CLASSIFICATION_RESULTS_DIR / "Results_GPT_6_Astra_Analysis" / "astra_full.jsonl"

if __name__ == "__main__":
    main(MODEL_NAME, OUTPUT_FILE, "Overnight GPT-6 Astra classifier")
