#!/usr/bin/env python3
"""
Overnight Sequential Benchmark Runner for 311 Hard & Discriminative Ranking Cases
================================================================================
Runs remaining frontier & next-gen LLM models sequentially on the 311 curated
cases (cases solved by 0, 1, or 2 LLMs + 19 unresolved cases) from RareDxBench:

  1. Gemini 4 Hydrogen (google/gemini-4-hydrogen)
  2. Gemini 4 Argon    (google/gemini-4-argon)
  3. GPT-6 Astra       (openai/gpt-6-astra)
  4. Gemini 3.8 Flash  (google/gemini-3.8-flash)

Features:
  - Runs one model after the other in a single chained queue
  - Bounded concurrency per model (default: 3) to prevent HTTP 429 rate limits
  - Strict fatal billing error detection (HTTP 402/quota) with graceful skip
  - Full resume support (skips already processed patient IDs in output file)
  - Immediate append-streaming per record (zero data loss on interruption)
  - Automatic sequential cleanup pass (concurrency=1) for transient network drops
  - Automatic evaluation summary JSON generation per model
  - Consolidated comparative scorecard generation at end of run
  - Detached background execution mode (--detach) with caffeinate on macOS

Usage:
  python scripts/run_311_remaining_models.py --detach
  python scripts/run_311_remaining_models.py --concurrency 3
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
PID_LOG_DIR = REPO_ROOT / "results" / "ranking"

MODEL_TARGETS = [
    {
        "label": "Gemini 4 Hydrogen",
        "model": "google/gemini-4-hydrogen",
        "output": REPO_ROOT / "results" / "ranking" / "Results_Gemini_4_Hydrogen" / "hydrogen_311_predictions.jsonl",
    },
    {
        "label": "Gemini 4 Argon",
        "model": "google/gemini-4-argon",
        "output": REPO_ROOT / "results" / "ranking" / "Results_Gemini_4_Argon" / "argon_311_predictions.jsonl",
    },
    {
        "label": "GPT-6 Astra",
        "model": "openai/gpt-6-astra",
        "output": REPO_ROOT / "results" / "ranking" / "Results_GPT_6_Astra" / "astra_311_predictions.jsonl",
    },
    {
        "label": "Gemini 3.8 Flash",
        "model": "google/gemini-3.8-flash",
        "output": REPO_ROOT / "results" / "ranking" / "Results_Gemini_3_8_Flash" / "gemini_flash_311_predictions.jsonl",
    },
]


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

    async with httpx.AsyncClient(verify=SSL_VERIFY) as client:
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

    log(
        f"COMPLETED {label}: {len(final_records)}/{len(all_patients)} | "
        f"empty={metrics.get('no_prediction')} | R@1={metrics.get('recall@1', 0):.1%} | "
        f"R@5={metrics.get('recall@5', 0):.1%} | MRR={metrics.get('mrr')}"
    )
    return {"label": label, "model": model, "n": len(final_records), "metrics": metrics}


def generate_consolidated_scorecard(results: list[dict], output_csv: Path) -> None:
    header = ["model", "n", "recall@1", "recall@3", "recall@5", "recall@10", "recall@20", "mrr", "no_prediction"]
    all_target_results = {}
    for target in MODEL_TARGETS:
        out_p = target["output"]
        if out_p.exists():
            records = []
            with open(out_p, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        try:
                            records.append(json.loads(line))
                        except Exception:
                            pass
            if records:
                m = compute_benchmark_metrics(records)
                all_target_results[target["label"]] = {
                    "label": target["label"],
                    "model": target["model"],
                    "n": len(records),
                    "metrics": m,
                }
    for r in results:
        all_target_results[r.get("label", r.get("model"))] = r

    with open(output_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        for label, r in all_target_results.items():
            m = r.get("metrics", {})
            writer.writerow([
                label,
                r.get("n", 0),
                f"{m.get('recall@1', 0):.4f}",
                f"{m.get('recall@3', 0):.4f}",
                f"{m.get('recall@5', 0):.4f}",
                f"{m.get('recall@10', 0):.4f}",
                f"{m.get('recall@20', 0):.4f}",
                f"{m.get('mrr', 0):.4f}",
                m.get("no_prediction", 0),
            ])
    log(f"Saved consolidated 311-case benchmark scorecard to: {output_csv}")


async def run_all(args: argparse.Namespace) -> None:
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

    selected = MODEL_TARGETS
    if args.models:
        wanted = {m.strip().lower() for m in args.models.split(",")}
        selected = [t for t in MODEL_TARGETS if any(w in t["label"].lower() or w in t["model"].lower() for w in wanted)]

    log("=" * 72)
    log(f"STARTING 311-CASE OVERNIGHT SEQUENTIAL BENCHMARK")
    log(f"Target Models ({len(selected)}): {[t['label'] for t in selected]}")
    log(f"Concurrency per model: {args.concurrency} | Delay: {args.delay}s")
    log("=" * 72)

    results = []
    for i, target in enumerate(selected, 1):
        log(f"\n>>> QUEUE ITEM {i}/{len(selected)}: {target['label']} <<<")
        try:
            res = await run_model(
                target=target,
                all_patients=patients_311,
                api_url=api_url,
                api_key=api_key,
                concurrency=args.concurrency,
                max_retries=args.max_retries,
                delay=args.delay,
            )
            results.append(res)
        except Exception as exc:
            log(f"ERROR running {target['label']}: {exc}. Proceeding to next model in queue...")

    log("\n" + "=" * 72)
    log("ALL 311-CASE QUEUED MODELS COMPLETED")
    for r in results:
        m = r["metrics"]
        log(f"  {r['label']:20s}: n={r['n']:3d} | empty={m.get('no_prediction'):2d} | R@1={m.get('recall@1', 0):.1%} | MRR={m.get('mrr')}")
    log("=" * 72)

    scorecard_path = REPO_ROOT / "results" / "ranking" / "comparison_311_hard_scorecard.csv"
    generate_consolidated_scorecard(results, scorecard_path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Overnight sequential benchmark runner for 311 hard ranking cases")
    parser.add_argument("--concurrency", type=int, default=3, help="Concurrent requests per model (default: 3)")
    parser.add_argument("--delay", type=float, default=0.5, help="Pause seconds between requests (default: 0.5)")
    parser.add_argument("--max-retries", type=int, default=8, help="Max retries per call (default: 8)")
    parser.add_argument("--models", type=str, default="", help="Optional comma-separated model filter")
    parser.add_argument("--detach", action="store_true", help="Launch detached background daemon (caffeinate on macOS)")
    args = parser.parse_args()

    if args.detach:
        log_path = PID_LOG_DIR / "run_311_overnight.log"
        pid_path = PID_LOG_DIR / "run_311_overnight.pid"
        PID_LOG_DIR.mkdir(parents=True, exist_ok=True)

        inner_cmd = [
            sys.executable, "-u", str(Path(__file__).resolve()),
            "--concurrency", str(args.concurrency),
            "--delay", str(args.delay),
            "--max-retries", str(args.max_retries),
        ]
        if args.models:
            inner_cmd.extend(["--models", args.models])

        # On macOS, wrap with caffeinate -is so Mac won't sleep with lid closed
        cmd = ["caffeinate", "-is"] + inner_cmd if sys.platform == "darwin" else inner_cmd

        with open(log_path, "a", encoding="utf-8") as lf:
            lf.write(f"\n{'='*72}\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Starting detached overnight sequential runner (311 cases)\n{'='*72}\n")
            proc = subprocess.Popen(cmd, stdout=lf, stderr=subprocess.STDOUT, start_new_session=True)

        pid_path.write_text(str(proc.pid), encoding="utf-8")
        print(f"Detached background process started with PID {proc.pid}")
        print(f"Log:  {log_path}")
        print(f"PID:  {pid_path}")
        return

    asyncio.run(run_all(args))


if __name__ == "__main__":
    main()
