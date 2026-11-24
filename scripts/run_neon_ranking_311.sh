#!/usr/bin/env bash
cd /Users/arunbodd/Documents/Work/AIVA_Benchmarking/RareDxBench
mkdir -p results/ranking/Results_Gemini_4_Neon
exec caffeinate -is python3 -u scripts/run_ranking_311_frontier.py --model neon --concurrency 3 > results/ranking/Results_Gemini_4_Neon/neon_311_run.log 2>&1
