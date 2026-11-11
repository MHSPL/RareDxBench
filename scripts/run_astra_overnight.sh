#!/bin/bash
# Overnight run of GPT-6 Astra (openai/gpt-6-astra) on the 2,894 benchmark variants.
# Set PYTHON_BIN to choose the interpreter (default: python3).
exec "$(dirname "$0")/launch_overnight.sh" scripts/run_astra_overnight.py \
    results/classification/Results_GPT_6_Astra_Analysis astra_run.log astra_run \
    "GPT-6 Astra (openai/gpt-6-astra)"
