#!/bin/bash
# Overnight run of Gemini 4 Argon (google/gemini-4-argon) on the 2,894 benchmark variants.
# Set PYTHON_BIN to choose the interpreter (default: python3).
exec "$(dirname "$0")/launch_overnight.sh" scripts/run_argon_overnight.py \
    results/classification/Results_Gemini_4_Argon_Analysis argon_run.log argon_run \
    "Gemini 4 Argon (google/gemini-4-argon)"
