#!/bin/bash
# Classification of the 497 benchmark variants with GPT-6.2 Astra (openai/gpt-6.2-astra).
exec "$(dirname "$0")/launch_overnight.sh" scripts/run_astra62_overnight.py \
    results/classification/Results_GPT_6_2_Astra_Analysis astra62_run.log astra62_run \
    "GPT-6.2 Astra (openai/gpt-6.2-astra)"
