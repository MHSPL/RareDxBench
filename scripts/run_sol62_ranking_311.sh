#!/usr/bin/env bash
cd /Users/arunbodd/Documents/Work/AIVA_Benchmarking/RareDxBench
mkdir -p results/ranking/Results_GPT_6_2_Sol
exec caffeinate -is python3 -u scripts/run_ranking_311_frontier.py --model sol62 --concurrency 3 > results/ranking/Results_GPT_6_2_Sol/sol62_311_run.log 2>&1
