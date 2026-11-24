#!/usr/bin/env python3
"""
Classification of the 497 benchmark variants with Gemini 4 Neon
(google/gemini-4-neon). The run logic lives in scripts/overnight_runner.py.

Usage:
    python scripts/run_neon_overnight.py [--input FILE] [--concurrency 5] [--max-passes 4]
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import CLASSIFICATION_RESULTS_DIR
from scripts.overnight_runner import main

MODEL_NAME = "google/gemini-4-neon"
OUTPUT_FILE = CLASSIFICATION_RESULTS_DIR / "Results_Gemini_4_Neon_Analysis" / "neon_full.jsonl"

if __name__ == "__main__":
    main(MODEL_NAME, OUTPUT_FILE, "Gemini 4 Neon classifier", default_concurrency=4)
