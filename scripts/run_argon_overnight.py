#!/usr/bin/env python3
"""
Overnight classification of the 2,894 benchmark variants with Gemini 4 Argon
(google/gemini-4-argon). The run logic lives in scripts/overnight_runner.py.

Usage:
    python scripts/run_argon_overnight.py [--input FILE] [--concurrency 7] [--max-passes 4]
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import CLASSIFICATION_RESULTS_DIR
from scripts.overnight_runner import main

MODEL_NAME = "google/gemini-4-argon"
OUTPUT_FILE = CLASSIFICATION_RESULTS_DIR / "Results_Gemini_4_Argon_Analysis" / "argon_full.jsonl"

if __name__ == "__main__":
    main(MODEL_NAME, OUTPUT_FILE, "Overnight Gemini 4 Argon classifier")
