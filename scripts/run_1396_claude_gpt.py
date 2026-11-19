#!/usr/bin/env python3
"""
Full 1,396-Patient Gene Prioritization Runner (Claude & GPT Models)
===================================================================
Completes the remaining patients across all 1,396 simulated cases for:
  1. Claude Haiku 5.5  (retries any empty rows in haiku_ranking_predictions.jsonl)
  2. Claude Sonnet 5.5 (seeds from sonnet_500_predictions.jsonl, runs remaining 896)
  3. Claude Opus 5.5   (seeds from opus_500_predictions.jsonl, runs remaining 896)
  4. GPT-6.1 Sol       (seeds from sol_500_predictions.jsonl, runs remaining 896)

Runs one model after the other with bounded concurrency, exponential backoff on
429/5xx, strict fatal billing detection (HTTP 402/quota), immediate streaming
persistence, and an automatic sequential (concurrency=1) cleanup pass for any
transient failures.

Usage:
  python scripts/run_1396_claude_gpt.py --detach
  python scripts/run_1396_claude_gpt.py --concurrency 2
"""

import argparse
import asyncio
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
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

MODEL_TARGETS = [
    {
        "label": "Claude Haiku 5.5",
        "model": "anthropic/claude-haiku-5-5",
        "seed_500": REPO_ROOT / "results" / "ranking" / "Results_Claude_Haiku_5_5" / "haiku_500_predictions.jsonl",
        "output": REPO_ROOT / "results" / "ranking" / "Results_Claude_Haiku_5_5" / "haiku_ranking_predictions.jsonl",
    },
    {
        "label": "Claude Sonnet 5.5",
        "model": "anthropic/claude-sonnet-5-5",
        "seed_500": REPO_ROOT / "results" / "ranking" / "Results_Claude_Sonnet_5_5" / "sonnet_500_predictions.jsonl",
        "output": REPO_ROOT / "results" / "ranking" / "Results_Claude_Sonnet_5_5" / "sonnet_ranking_predictions.jsonl",
    },
    {
        "label": "Claude Opus 5.5",
        "model": "anthropic/claude-opus-5-5",
        "seed_500": REPO_ROOT / "results" / "ranking" / "Results_Claude_Opus_5_5" / "opus_500_predictions.jsonl",
        "output": REPO_ROOT / "results" / "ranking" / "Results_Claude_Opus_5_5" / "opus_ranking_predictions.jsonl",
    },
    {
        "label": "GPT-6.1 Sol",
        "model": "openai/gpt-6.1-sol",
        "seed_500": REPO_ROOT / "results" / "ranking" / "Results_GPT_6_1_Sol" / "sol_500_predictions.jsonl",
        "output": REPO_ROOT / "results" / "ranking" / "Results_GPT_6_1_Sol" / "sol_ranking_predictions.jsonl",
    },
]


def log(msg: str) -> None:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] {msg}", flush=True)


def load_seeded_records(output_path: Path, seed_500: Path | None) -> dict[int, dict]:
    """Load non-empty existing records from output_path, seeding from seed_500 if needed."""
    records_by_pid: dict[int, dict] = {}

    if seed_500 and seed_500.exists():
        with open(seed_500, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                r = json.loads(line)
                if r.get("predicted"):
                    records_by_pid[int(r["patient_id"])] = r

    if output_path.exists():
        with open(output_path, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                r = json.loads(line)
                if r.get("predicted"):
                    records_by_pid[int(r["patient_id"])] = r

    return records_by_pid


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
    """Run a batch of patients and stream resolved records immediately."""
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
                        log(f"  [{counter['done']}/{total}] Patient {pid}: UNRESOLVED ({reason}) — queued for cleanup")

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
    seed_500: Path | None = target.get("seed_500")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    records_by_pid = load_seeded_records(output_path, seed_500)

    # Rewrite deduplicated clean seed snapshot before appending new calls
    ordered_existing = [records_by_pid[p["patient_id"]] for p in all_patients if p["patient_id"] in records_by_pid]
    write_jsonl_atomic(output_path, ordered_existing)

    remaining = [p for p in all_patients if p["patient_id"] not in records_by_pid]

    log("=" * 72)
    log(f"MODEL: {label} ({model})")
    log(f"Output: {output_path}")
    log(f"Seeded/Valid: {len(records_by_pid)} / {len(all_patients)} | Remaining: {len(remaining)}")

    if remaining:
        log(f"Pass 1: running {len(remaining)} patients (concurrency={concurrency})...")
        _, still_unresolved = await run_batch(
            remaining, model, api_url, api_key, output_path, records_by_pid,
            concurrency=concurrency, max_retries=max_retries, delay=delay,
            record_explicit_empty=False,
        )

        if still_unresolved:
            log(f"Pass 2 (sequential cleanup): retrying {len(still_unresolved)} unresolved patients (concurrency=1)...")
            _, final_unresolved = await run_batch(
                still_unresolved, model, api_url, api_key, output_path, records_by_pid,
                concurrency=1, max_retries=max_retries, delay=max(1.0, delay),
                record_explicit_empty=True,
            )
            for p in final_unresolved:
                records_by_pid[p["patient_id"]] = build_record(p, model, [], {})

    # Final canonical sort by all_patients order
    final_records = [records_by_pid[p["patient_id"]] for p in all_patients if p["patient_id"] in records_by_pid]
    write_jsonl_atomic(output_path, final_records)

    metrics = compute_benchmark_metrics(final_records)
    summary_path = output_path.parent / f"{output_path.stem}_evaluation_summary.json"
    summary_payload = {
        "model": model,
        "ablation": "hpo+vcf+genes+disease",
        "n_evaluated": len(final_records),
        "sample_stratified": False,
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
    return {"label": label, "n": len(final_records), "metrics": metrics}


async def run_all(args: argparse.Namespace) -> None:
    api_url = os.getenv("AIVA_API_URL", "https://api.aivaportal.com/agents/chat/stream").rstrip("/")
    if "/agents/chat/stream" not in api_url:
        api_url = f"{api_url}/agents/chat/stream"
    api_key = os.getenv("AIVA_API_KEY") or os.getenv("AIVA_API_TOKEN", "")
    if not api_key:
        sys.exit("ERROR: AIVA_API_TOKEN / AIVA_API_KEY is not set.")

    all_patients = load_dataset(DEFAULT_PROMPTS, DEFAULT_ANSWERS, sample_file=None, sample_size=0)
    log(f"Loaded {len(all_patients)} total patients for full 1,396-cohort benchmark.")

    selected = MODEL_TARGETS
    if args.models:
        wanted = {m.strip().lower() for m in args.models.split(",")}
        selected = [t for t in MODEL_TARGETS if any(w in t["label"].lower() or w in t["model"].lower() for w in wanted)]

    results = []
    for target in selected:
        res = await run_model(
            target=target,
            all_patients=all_patients,
            api_url=api_url,
            api_key=api_key,
            concurrency=args.concurrency,
            max_retries=args.max_retries,
            delay=args.delay,
        )
        results.append(res)

    log("\n" + "=" * 72)
    log("FULL 1,396-COHORT BENCHMARK COMPLETE")
    for r in results:
        m = r["metrics"]
        log(f"  {r['label']:18s}: n={r['n']:4d} | empty={m.get('no_prediction'):2d} | R@1={m.get('recall@1', 0):.1%} | MRR={m.get('mrr')}")
    log("=" * 72)


def main() -> None:
    parser = argparse.ArgumentParser(description="Complete 1,396-patient ranking benchmark for Claude & GPT models")
    parser.add_argument("--concurrency", type=int, default=2, help="Concurrent requests per model (default: 2)")
    parser.add_argument("--delay", type=float, default=0.5, help="Pause seconds after each call (default: 0.5)")
    parser.add_argument("--max-retries", type=int, default=8, help="Max retries per call (default: 8)")
    parser.add_argument("--models", type=str, default="", help="Optional comma-separated model filter (e.g. haiku,sonnet)")
    parser.add_argument("--detach", action="store_true", help="Launch detached background job")
    args = parser.parse_args()

    if args.detach:
        log_path = REPO_ROOT / "results" / "ranking" / "run_1396_claude_gpt.log"
        pid_path = REPO_ROOT / "results" / "ranking" / "run_1396_claude_gpt.pid"
        cmd = [
            sys.executable, "-u", str(Path(__file__).resolve()),
            "--concurrency", str(args.concurrency),
            "--delay", str(args.delay),
            "--max-retries", str(args.max_retries),
        ]
        if args.models:
            cmd.extend(["--models", args.models])
        with open(log_path, "a", encoding="utf-8") as lf:
            lf.write(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Starting 1,396-patient sequential run\n")
            proc = subprocess.Popen(cmd, stdout=lf, stderr=subprocess.STDOUT, start_new_session=True)
        pid_path.write_text(str(proc.pid), encoding="utf-8")
        print(f"Started detached 1,396 run PID {proc.pid}")
        print(f"Log: {log_path}")
        print(f"PID: {pid_path}")
        return

    asyncio.run(run_all(args))


if __name__ == "__main__":
    main()
