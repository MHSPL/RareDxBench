#!/usr/bin/env python3
"""
Overnight classification of the 2,894 benchmark variants with Gemini 4 Hydrogen
(google/gemini-4-hydrogen). The run logic lives in scripts/overnight_runner.py.

Usage:
    python scripts/run_hydrogen_overnight.py [--input FILE] [--concurrency 7] [--max-passes 4]
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import CLASSIFICATION_RESULTS_DIR
from scripts.overnight_runner import main

MODEL_NAME = "google/gemini-4-hydrogen"
OUTPUT_FILE = CLASSIFICATION_RESULTS_DIR / "Results_Gemini_4_Hydrogen_Analysis" / "hydrogen_full.jsonl"

if __name__ == "__main__":
    main(MODEL_NAME, OUTPUT_FILE, "Overnight Gemini 4 Hydrogen classifier", default_concurrency=7)
