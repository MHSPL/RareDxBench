#!/bin/bash
# Classification of the 497 benchmark variants with GPT-6.2 Sol (openai/gpt-6.2-sol).
exec "$(dirname "$0")/launch_overnight.sh" scripts/run_sol62_overnight.py \
    results/classification/Results_GPT_6_2_Sol_Analysis sol62_run.log sol62_run \
    "GPT-6.2 Sol (openai/gpt-6.2-sol)"
