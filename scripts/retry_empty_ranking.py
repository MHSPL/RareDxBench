#!/usr/bin/env python3
"""
Sequential Retry Runner for Unresolved (Empty) Gene Ranking Predictions
======================================================================
Retries all patients with empty `predicted` lists across the 500-patient
stratified runs, one model after the other, at low concurrency with strong
exponential backoff so transient 429 / stream errors resolve cleanly.

Models processed sequentially:
  1. Claude Haiku 5.5  (results/ranking/Results_Claude_Haiku_5_5/haiku_500_predictions.jsonl)
  2. Claude Sonnet 5.5 (results/ranking/Results_Claude_Sonnet_5_5/sonnet_500_predictions.jsonl)
  3. Claude Opus 5.5   (results/ranking/Results_Claude_Opus_5_5/opus_500_predictions.jsonl)
  4. GPT-6.1 Sol       (results/ranking/Results_GPT_6_1_Sol/sol_500_predictions.jsonl)

Usage:
  python scripts/retry_empty_ranking.py --detach
  python scripts/retry_empty_ranking.py --concurrency 1 --delay 1.5
"""

import argparse
import asyncio
import json
import os
import random
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts.run_opus_ranking import (
    DEFAULT_ANSWERS,
    DEFAULT_PROMPTS,
    SSL_VERIFY,
    compute_benchmark_metrics,
    find_rank,
    is_fatal_billing_error,
    load_dataset,
    parse_predictions,
)

MODEL_TARGETS = [
    {
        "label": "Claude Haiku 5.5",
        "model": "anthropic/claude-haiku-5-5",
        "output": REPO_ROOT / "results" / "ranking" / "Results_Claude_Haiku_5_5" / "haiku_500_predictions.jsonl",
        "extra_sync": REPO_ROOT / "results" / "ranking" / "Results_Claude_Haiku_5_5" / "haiku_ranking_predictions.jsonl",
    },
    {
        "label": "Claude Sonnet 5.5",
        "model": "anthropic/claude-sonnet-5-5",
        "output": REPO_ROOT / "results" / "ranking" / "Results_Claude_Sonnet_5_5" / "sonnet_500_predictions.jsonl",
        "extra_sync": None,
    },
    {
        "label": "Claude Opus 5.5",
        "model": "anthropic/claude-opus-5-5",
        "output": REPO_ROOT / "results" / "ranking" / "Results_Claude_Opus_5_5" / "opus_500_predictions.jsonl",
        "extra_sync": None,
    },
    {
        "label": "GPT-6.1 Sol",
        "model": "openai/gpt-6.1-sol",
        "output": REPO_ROOT / "results" / "ranking" / "Results_GPT_6_1_Sol" / "sol_500_predictions.jsonl",
        "extra_sync": None,
    },
]


def log(msg: str) -> None:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] {msg}", flush=True)


async def call_and_parse(
    client: httpx.AsyncClient,
    api_url: str,
    api_key: str,
    model: str,
    prompt: str,
    max_retries: int = 8,
) -> tuple[list[str], dict[str, float], str]:
    """Call AIVA streaming API with robust backoff and parse predictions.

    Returns (predicted_genes, scores, status_reason).
    """
    headers = {"Authorization": f"Bearer {api_key}"}
    data = {"message": prompt, "model": model, "stream": "false"}
    last_reason = "unknown_failure"

    for attempt in range(1, max_retries + 1):
        try:
            async with client.stream("POST", api_url, data=data, headers=headers, timeout=240.0) as resp:
                if resp.status_code == 429:
                    retry_after = resp.headers.get("Retry-After")
                    base_delay = float(retry_after) if retry_after and retry_after.isdigit() else min(120, 15 * attempt)
                    delay = base_delay + random.uniform(1.0, 5.0)
                    last_reason = f"http_429_rate_limit"
                    log(f"    [429 Rate Limit] Backing off {delay:.1f}s (attempt {attempt}/{max_retries})...")
                    await asyncio.sleep(delay)
                    continue

                if is_fatal_billing_error(resp.status_code, ""):
                    body = (await resp.aread()).decode("utf-8", errors="replace")
                    log(f"FATAL BILLING ERROR (HTTP {resp.status_code}): {body[:300]}")
                    raise SystemExit(f"Fatal billing error (HTTP {resp.status_code}): {body[:200]}")

                if 500 <= resp.status_code < 600:
                    delay = min(90, 10 * attempt) + random.uniform(0.5, 3.0)
                    last_reason = f"http_{resp.status_code}"
                    log(f"    [HTTP {resp.status_code}] Retrying in {delay:.1f}s (attempt {attempt}/{max_retries})...")
                    await asyncio.sleep(delay)
                    continue

                if resp.status_code != 200:
                    body = (await resp.aread()).decode("utf-8", errors="replace")
                    if is_fatal_billing_error(resp.status_code, body):
                        log(f"FATAL BILLING ERROR (HTTP {resp.status_code}): {body[:300]}")
                        raise SystemExit(f"Fatal billing error (HTTP {resp.status_code}): {body[:200]}")
                    last_reason = f"http_{resp.status_code}: {body[:160]}"
                    log(f"    API error {resp.status_code}: {body[:200]}")
                    return [], {}, last_reason

                text = ""
                stream_error = None
                async for line in resp.aiter_lines():
                    line = line.strip()
                    if not line or not line.startswith("data: "):
                        continue
                    payload = line[6:]
                    if payload == "[DONE]":
                        break
                    try:
                        ev = json.loads(payload)
                        if ev.get("error") or ev.get("type") == "error":
                            err_str = str(ev.get("error") or ev.get("content") or ev)
                            if is_fatal_billing_error(resp.status_code, err_str):
                                log(f"FATAL BILLING ERROR in stream: {err_str[:300]}")
                                raise SystemExit(f"Fatal billing error in stream: {err_str[:200]}")
                            stream_error = err_str
                        if ev.get("type") in ("text_delta", "final_output"):
                            text += ev.get("content", "")
                    except json.JSONDecodeError:
                        pass

                if stream_error and not text.strip():
                    delay = min(90, 15 * attempt) + random.uniform(1.0, 4.0)
                    last_reason = f"stream_error: {stream_error[:160]}"
                    log(f"    [Stream Error] {stream_error[:120]} — retrying in {delay:.1f}s (attempt {attempt}/{max_retries})")
                    await asyncio.sleep(delay)
                    continue

                predicted, scores = parse_predictions(text)
                if predicted:
                    return predicted, scores, "resolved"

                # Check if model explicitly returned empty predicted_genes or failed to emit JSON
                if '"predicted_genes"' in text:
                    last_reason = "explicit_empty_predicted_genes"
                    if attempt < min(3, max_retries):
                        log(f"    Model returned empty predicted_genes; confirming with 1 retry (attempt {attempt})...")
                        await asyncio.sleep(3.0)
                        continue
                    return [], {}, last_reason

                last_reason = f"empty_or_unparseable_response (len={len(text)}, preview={text[:120]!r})"
                delay = min(60, 10 * attempt) + random.uniform(0.5, 2.0)
                log(f"    [Unparseable/Empty] {last_reason} — retrying in {delay:.1f}s (attempt {attempt}/{max_retries})")
                await asyncio.sleep(delay)

        except SystemExit:
            raise
        except Exception as exc:
            last_reason = f"exception: {exc}"
            if attempt == max_retries:
                log(f"    Request failed after {max_retries} attempts: {exc}")
                return [], {}, last_reason
            delay = min(90, 10 * attempt) + random.uniform(0.5, 3.0)
            log(f"    [Exception] {exc} — retrying in {delay:.1f}s (attempt {attempt}/{max_retries})")
            await asyncio.sleep(delay)

    return [], {}, last_reason


def write_jsonl_atomic(path: Path, records: list[dict]) -> None:
    tmp = path.with_suffix(".jsonl.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    tmp.replace(path)


def sync_extra_jsonl(extra_path: Path, updated_by_pid: dict[int, dict]) -> None:
    if not extra_path or not extra_path.exists() or not updated_by_pid:
        return
    bak = extra_path.with_suffix(".jsonl.bak")
    if not bak.exists():
        shutil.copy2(extra_path, bak)
    rows = []
    synced = 0
    with open(extra_path, "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            r = json.loads(line)
            pid = int(r["patient_id"])
            if pid in updated_by_pid and not r.get("predicted"):
                rows.append(updated_by_pid[pid])
                synced += 1
            else:
                rows.append(r)
    if synced:
        write_jsonl_atomic(extra_path, rows)
        log(f"  Synced {synced} resolved prediction(s) into {extra_path.name}")


async def process_model_target(
    target: dict,
    prompt_by_pid: dict[int, dict],
    api_url: str,
    api_key: str,
    concurrency: int,
    delay: float,
    max_retries: int,
) -> dict:
    label = target["label"]
    model = target["model"]
    output_path: Path = target["output"]

    log("=" * 72)
    log(f"MODEL: {label} ({model})")
    log(f"File:  {output_path}")

    records = [json.loads(l) for l in open(output_path, "r", encoding="utf-8") if l.strip()]
    empty_indices = [i for i, r in enumerate(records) if not r.get("predicted")]
    log(f"Found {len(empty_indices)} empty/unresolved patients out of {len(records)}.")

    if not empty_indices:
        return {"label": label, "before_empty": 0, "resolved": 0, "still_empty": 0, "metrics": None}

    bak_path = output_path.with_suffix(".jsonl.bak")
    if not bak_path.exists():
        shutil.copy2(output_path, bak_path)
        log(f"Created backup: {bak_path.name}")

    sem = asyncio.Semaphore(concurrency)
    resolved_count = 0
    still_empty_reasons: list[tuple[int, str]] = []
    updated_by_pid: dict[int, dict] = {}

    async with httpx.AsyncClient(verify=SSL_VERIFY) as client:
        for idx_num, rec_idx in enumerate(empty_indices, 1):
            rec = records[rec_idx]
            pid = int(rec["patient_id"])
            pinfo = prompt_by_pid.get(pid)
            if not pinfo:
                log(f"  [{idx_num}/{len(empty_indices)}] Patient {pid}: prompt not found in dataset, skipping.")
                still_empty_reasons.append((pid, "prompt_not_found"))
                continue

            log(f"  [{idx_num}/{len(empty_indices)}] Retrying Patient {pid} ({rec.get('broad_category', '')})...")
            async with sem:
                predicted, scores, reason = await call_and_parse(
                    client, api_url, api_key, model, pinfo["prompt"], max_retries=max_retries
                )
                if delay > 0:
                    await asyncio.sleep(delay)

            if predicted:
                true_genes = rec.get("true_genes", pinfo["true_genes"])
                rank = find_rank(predicted, true_genes)
                rec["predicted"] = predicted
                rec["predicted_scores"] = scores
                rec["rank_of_true_gene"] = rank
                rec["timestamp"] = datetime.now(timezone.utc).isoformat()
                records[rec_idx] = rec
                updated_by_pid[pid] = rec
                resolved_count += 1

                top_gene = predicted[0]
                top_score = scores.get(top_gene, 0.0)
                status = "✓ RANK_1" if rank == 1 else (f"RANK_{rank}" if rank is not None else "MISSED")
                log(
                    f"    -> RESOLVED: Top={top_gene}(s={top_score:.2f}) | "
                    f"True={true_genes[0] if true_genes else '?'}(rank={rank}) | {status}"
                )
                # Persist progress immediately after each resolved case
                write_jsonl_atomic(output_path, records)
            else:
                still_empty_reasons.append((pid, reason))
                log(f"    -> UNRESOLVED ({reason})")

    if target.get("extra_sync") and updated_by_pid:
        sync_extra_jsonl(target["extra_sync"], updated_by_pid)

    metrics = compute_benchmark_metrics(records)
    summary_path = output_path.parent / f"{output_path.stem}_evaluation_summary.json"
    summary_payload = {
        "model": model,
        "ablation": "hpo+vcf+genes+disease",
        "n_evaluated": len(records),
        "sample_stratified": True,
        "metrics": metrics,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary_payload, f, indent=2)

    log(
        f"Summary for {label}: resolved {resolved_count}/{len(empty_indices)} | "
        f"still_empty={len(still_empty_reasons)} | "
        f"Recall@1={metrics.get('recall@1', 0):.1%} | MRR={metrics.get('mrr')}"
    )
    return {
        "label": label,
        "before_empty": len(empty_indices),
        "resolved": resolved_count,
        "still_empty": len(still_empty_reasons),
        "unresolved_details": still_empty_reasons,
        "metrics": metrics,
    }


async def run_all(args: argparse.Namespace) -> None:
    api_url = os.getenv("AIVA_API_URL", "https://api.aivaportal.com/agents/chat/stream").rstrip("/")
    if "/agents/chat/stream" not in api_url:
        api_url = f"{api_url}/agents/chat/stream"
    api_key = os.getenv("AIVA_API_KEY") or os.getenv("AIVA_API_TOKEN", "")
    if not api_key:
        sys.exit("ERROR: AIVA_API_TOKEN / AIVA_API_KEY is not set.")

    log("Loading ranking prompts & answers...")
    all_patients = load_dataset(DEFAULT_PROMPTS, DEFAULT_ANSWERS, sample_file=None, sample_size=0)
    prompt_by_pid = {int(p["patient_id"]): p for p in all_patients}
    log(f"Loaded {len(prompt_by_pid)} patient prompts.")

    summaries = []
    for target in MODEL_TARGETS:
        res = await process_model_target(
            target=target,
            prompt_by_pid=prompt_by_pid,
            api_url=api_url,
            api_key=api_key,
            concurrency=args.concurrency,
            delay=args.delay,
            max_retries=args.max_retries,
        )
        summaries.append(res)

    log("\n" + "=" * 72)
    log("FINAL RETRY SUMMARY ACROSS ALL MODELS")
    log("=" * 72)
    for s in summaries:
        m = s.get("metrics") or {}
        r1 = f"{m.get('recall@1', 0):.1%}" if m else "n/a"
        mrr = f"{m.get('mrr', 0):.4f}" if m else "n/a"
        log(
            f"  {s['label']:20s}: before_empty={s['before_empty']:2d}, "
            f"resolved={s['resolved']:2d}, still_empty={s['still_empty']:2d} | R@1={r1}, MRR={mrr}"
        )
        for pid, reason in s.get("unresolved_details", []):
            log(f"    - Unresolved Patient {pid}: {reason}")
    log("=" * 72)


def main() -> None:
    parser = argparse.ArgumentParser(description="Sequentially retry empty ranking predictions across all models")
    parser.add_argument("--concurrency", type=int, default=1, help="Concurrency per model (default: 1)")
    parser.add_argument("--delay", type=float, default=1.0, help="Seconds to pause between calls (default: 1.0)")
    parser.add_argument("--max-retries", type=int, default=8, help="Max retries per patient on 429/5xx/empty (default: 8)")
    parser.add_argument("--detach", action="store_true", help="Run detached in background")
    args = parser.parse_args()

    if args.detach:
        log_path = REPO_ROOT / "results" / "ranking" / "retry_empty_ranking.log"
        pid_path = REPO_ROOT / "results" / "ranking" / "retry_empty_ranking.pid"
        cmd = [
            sys.executable, "-u", str(Path(__file__).resolve()),
            "--concurrency", str(args.concurrency),
            "--delay", str(args.delay),
            "--max-retries", str(args.max_retries),
        ]
        with open(log_path, "a", encoding="utf-8") as lf:
            lf.write(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Starting sequential retry job\n")
            proc = subprocess.Popen(cmd, stdout=lf, stderr=subprocess.STDOUT, start_new_session=True)
        pid_path.write_text(str(proc.pid), encoding="utf-8")
        print(f"Started detached retry job PID {proc.pid}")
        print(f"Log: {log_path}")
        print(f"PID: {pid_path}")
        return

    asyncio.run(run_all(args))


if __name__ == "__main__":
    main()
