#!/bin/bash
# Classification of the 497 benchmark variants with Gemini 4 Neon (google/gemini-4-neon).
exec "$(dirname "$0")/launch_overnight.sh" scripts/run_neon_overnight.py \
    results/classification/Results_Gemini_4_Neon_Analysis neon_run.log neon_run \
    "Gemini 4 Neon (google/gemini-4-neon)"
