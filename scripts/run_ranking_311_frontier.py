#!/usr/bin/env python3
"""
Benchmark Runner for 311 Hard & Discriminative Ranking Cases
(Gemini 4 Neon, GPT-6.2 Sol, GPT-6.2 Astra)
============================================================
Runs frontier LLM models on the 311 curated benchmark cases from RareDxBench:
  - Gemini 4 Neon (google/gemini-4-neon)
  - GPT-6.2 Sol   (openai/gpt-6.2-sol)
  - GPT-6.2 Astra (openai/gpt-6.2-astra)

Usage:
  python scripts/run_ranking_311_frontier.py --model neon --concurrency 3
  python scripts/run_ranking_311_frontier.py --model sol62 --concurrency 3
  python scripts/run_ranking_311_frontier.py --model astra62 --concurrency 3
  python scripts/run_ranking_311_frontier.py --model all --concurrency 3
"""

import argparse
import asyncio
import csv
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx
from dotenv import load_dotenv

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
load_dotenv(REPO_ROOT / ".env")
sys.path.insert(0, str(REPO_ROOT))

from scripts.retry_empty_ranking import call_and_parse, write_jsonl_atomic
from scripts.run_opus_ranking import (
    DEFAULT_ANSWERS,
    DEFAULT_PROMPTS,
    SSL_VERIFY,
    compute_benchmark_metrics,
    find_rank,
    load_dataset,
)

SAMPLE_311_FILE = REPO_ROOT / "data" / "ranking" / "selected_311_patients.json"

MODEL_MAP = {
    "neon": {
        "key": "neon",
        "label": "Gemini 4 Neon",
        "model": "google/gemini-4-neon",
        "output": REPO_ROOT / "results" / "ranking" / "Results_Gemini_4_Neon" / "neon_311_predictions.jsonl",
    },
    "sol62": {
        "key": "sol62",
        "label": "GPT-6.2 Sol",
        "model": "openai/gpt-6.2-sol",
        "output": REPO_ROOT / "results" / "ranking" / "Results_GPT_6_2_Sol" / "sol62_311_predictions.jsonl",
    },
    "astra62": {
        "key": "astra62",
        "label": "GPT-6.2 Astra",
        "model": "openai/gpt-6.2-astra",
        "output": REPO_ROOT / "results" / "ranking" / "Results_GPT_6_2_Astra" / "astra62_311_predictions.jsonl",
    },
}


def log(msg: str) -> None:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] {msg}", flush=True)


def load_existing_records(output_path: Path) -> dict[int, dict]:
    records: dict[int, dict] = {}
    if output_path.exists():
        with open(output_path, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    r = json.loads(line)
                    if r.get("predicted"):
                        records[int(r["patient_id"])] = r
                except Exception:
                    pass
    return records


def build_record(patient: dict, model: str, predicted: list[str], scores: dict[str, float]) -> dict:
    true_genes = patient["true_genes"]
    rank = find_rank(predicted, true_genes) if predicted else None
    return {
        "patient_id": patient["patient_id"],
        "disease_id": patient["disease_id"],
        "ablation": "hpo+vcf+genes+disease",
        "model": model,
        "true_genes": true_genes,
        "true_genes_ensg": patient["true_genes_ensg"],
        "predicted": predicted,
        "predicted_scores": scores,
        "rank_of_true_gene": rank,
        "category": patient["category"],
        "broad_category": patient["broad_category"],
        "in_gene_gene_kg": patient["in_gene_gene_kg"],
        "in_disease_gene_kg": patient["in_disease_gene_kg"],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


async def run_batch(
    patients: list[dict],
    model: str,
    api_url: str,
    api_key: str,
    output_path: Path,
    records_by_pid: dict[int, dict],
    concurrency: int,
    max_retries: int,
    delay: float,
    record_explicit_empty: bool = False,
) -> tuple[int, list[dict]]:
    sem = asyncio.Semaphore(concurrency)
    write_lock = asyncio.Lock()
    counter = {"done": 0, "resolved": 0}
    total = len(patients)
    unresolved: list[dict] = []

    async with httpx.AsyncClient(verify=SSL_VERIFY, timeout=300.0) as client:
        with open(output_path, "a", encoding="utf-8") as fout:
            async def process_one(patient: dict):
                pid = patient["patient_id"]
                async with sem:
                    predicted, scores, reason = await call_and_parse(
                        client, api_url, api_key, model, patient["prompt"], max_retries=max_retries
                    )
                    if delay > 0:
                        await asyncio.sleep(delay)

                async with write_lock:
                    counter["done"] += 1
                    if predicted or (record_explicit_empty and reason == "explicit_empty_predicted_genes"):
                        rec = build_record(patient, model, predicted, scores)
                        records_by_pid[pid] = rec
                        fout.write(json.dumps(rec, ensure_ascii=False) + "\n")
                        fout.flush()
                        if predicted:
                            counter["resolved"] += 1
                            top_g = predicted[0]
                            top_s = scores.get(top_g, 0.0)
                            rk = rec["rank_of_true_gene"]
                            st = "✓ RANK_1" if rk == 1 else (f"RANK_{rk}" if rk is not None else "MISSED")
                            log(f"  [{counter['done']}/{total}] Patient {pid} ({patient['broad_category']}): Top={top_g}(s={top_s:.2f}) | True={patient['true_genes'][0]}(rank={rk}) | {st}")
                        else:
                            log(f"  [{counter['done']}/{total}] Patient {pid}: recorded explicit empty ({reason})")
                    else:
                        unresolved.append(patient)
                        log(f"  [{counter['done']}/{total}] Patient {pid}: UNRESOLVED ({reason}) — queued for retry")

            await asyncio.gather(*(process_one(p) for p in patients))

    return counter["resolved"], unresolved


async def run_model(
    target: dict,
    all_patients: list[dict],
    api_url: str,
    api_key: str,
    concurrency: int,
    max_retries: int,
    delay: float,
) -> dict:
    label = target["label"]
    model = target["model"]
    output_path: Path = target["output"]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    records_by_pid = load_existing_records(output_path)

    # Rewrite deduplicated clean snapshot before appending new calls
    ordered_existing = [records_by_pid[p["patient_id"]] for p in all_patients if p["patient_id"] in records_by_pid]
    write_jsonl_atomic(output_path, ordered_existing)

    remaining = [p for p in all_patients if p["patient_id"] not in records_by_pid]

    log("=" * 72)
    log(f"STARTING MODEL: {label} ({model})")
    log(f"Output: {output_path}")
    log(f"Already Completed: {len(records_by_pid)} / {len(all_patients)} | Remaining: {len(remaining)}")

    if remaining:
        log(f"Pass 1: running {len(remaining)} remaining cases (concurrency={concurrency})...")
        _, still_unresolved = await run_batch(
            remaining, model, api_url, api_key, output_path, records_by_pid,
            concurrency=concurrency, max_retries=max_retries, delay=delay,
            record_explicit_empty=False,
        )

        if still_unresolved:
            log(f"Pass 2 (cleanup pass): retrying {len(still_unresolved)} cases (concurrency=1)...")
            _, final_unresolved = await run_batch(
                still_unresolved, model, api_url, api_key, output_path, records_by_pid,
                concurrency=1, max_retries=max_retries, delay=max(1.0, delay),
                record_explicit_empty=True,
            )
            for p in final_unresolved:
                records_by_pid[p["patient_id"]] = build_record(p, model, [], {})

    final_records = [records_by_pid[p["patient_id"]] for p in all_patients if p["patient_id"] in records_by_pid]
    write_jsonl_atomic(output_path, final_records)

    metrics = compute_benchmark_metrics(final_records)
    summary_path = output_path.parent / f"{output_path.stem}_evaluation_summary.json"
    summary_payload = {
        "model": model,
        "label": label,
        "ablation": "hpo+vcf+genes+disease",
        "cohort": "311_hard_discriminative_subset",
        "n_evaluated": len(final_records),
        "metrics": metrics,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary_payload, f, indent=2)

    log(f"\nCompleted {label}: n={len(final_records)} | R@1={metrics.get('recall@1', 0):.1%} | MRR={metrics.get('mrr')}")
    log(f"Saved evaluation summary to: {summary_path}")
    return {"label": label, "model": model, "n": len(final_records), "metrics": metrics}


def update_dashboard():
    log("\n[Dashboard] Automatically rebuilding dashboard...")
    update_script = REPO_ROOT / "scripts" / "update_dashboard.py"
    if update_script.exists():
        subprocess.run([sys.executable, str(update_script)], cwd=str(REPO_ROOT))


async def main_async(args: argparse.Namespace) -> None:
    api_url = os.getenv("AIVA_API_URL", "https://api.aivaportal.com/agents/chat/stream").rstrip("/")
    if "/agents/chat/stream" not in api_url:
        api_url = f"{api_url}/agents/chat/stream"
    api_key = os.getenv("AIVA_API_KEY") or os.getenv("AIVA_API_TOKEN", "")
    if not api_key:
        sys.exit("ERROR: AIVA_API_TOKEN / AIVA_API_KEY is not set.")

    if not SAMPLE_311_FILE.exists():
        sys.exit(f"ERROR: Sample manifest {SAMPLE_311_FILE} does not exist.")

    log(f"Loading 311 hard patient manifest from {SAMPLE_311_FILE}...")
    patients_311 = load_dataset(DEFAULT_PROMPTS, DEFAULT_ANSWERS, sample_file=SAMPLE_311_FILE)
    log(f"Successfully loaded {len(patients_311)} target patients.")

    if args.model == "all":
        targets = list(MODEL_MAP.values())
    elif args.model in MODEL_MAP:
        targets = [MODEL_MAP[args.model]]
    else:
        sys.exit(f"Unknown model choice: {args.model}. Choose from {list(MODEL_MAP.keys())} or 'all'.")

    log("=" * 72)
    log(f"STARTING 311 RANKING RUN ({[t['label'] for t in targets]})")
    log(f"Concurrency: {args.concurrency} | Delay: {args.delay}s")
    log("=" * 72)

    for target in targets:
        await run_model(
            target=target,
            all_patients=patients_311,
            api_url=api_url,
            api_key=api_key,
            concurrency=args.concurrency,
            max_retries=args.max_retries,
            delay=args.delay,
        )

    if args.update_dashboard:
        update_dashboard()

    log("ALL REQUESTED RUNS FINISHED.")


def main():
    parser = argparse.ArgumentParser(description="311 Ranking Runner for Neon, Sol 6.2, Astra 6.2")
    parser.add_argument("--model", type=str, default="all", choices=["neon", "sol62", "astra62", "all"], help="Model to run")
    parser.add_argument("--concurrency", type=int, default=3, help="Concurrent requests (default: 3)")
    parser.add_argument("--delay", type=float, default=0.5, help="Pause seconds between requests (default: 0.5)")
    parser.add_argument("--max-retries", type=int, default=8, help="Max retries per call (default: 8)")
    parser.add_argument("--update-dashboard", action="store_true", default=True, help="Update dashboard after run finishes")
    args = parser.parse_args()

    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
