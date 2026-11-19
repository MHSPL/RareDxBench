#!/bin/bash
# Overnight run of Gemini 4 Hydrogen (google/gemini-4-hydrogen) on the 2,894 benchmark variants.
# Set PYTHON_BIN to choose the interpreter (default: python3).
exec "$(dirname "$0")/launch_overnight.sh" scripts/run_hydrogen_overnight.py \
    results/classification/Results_Gemini_4_Hydrogen_Analysis hydrogen_run.log hydrogen_run \
    "Gemini 4 Hydrogen (google/gemini-4-hydrogen)"
