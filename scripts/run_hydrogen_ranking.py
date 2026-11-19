#!/usr/bin/env python3
"""
Gene Prioritization Benchmark Runner for Gemini 4 Hydrogen
==========================================================
Runs Gemini 4 Hydrogen (google/gemini-4-hydrogen) on the canonical 500-sample
category-stratified cohort (100 per broad category) from the RareDxBench
variant ranking benchmark using prompts in data/ranking/ranking_prompts.jsonl
and ground truth answers in data/ranking/ranking_answers.jsonl.

Features:
  - Strict fatal billing error handling (HTTP 402, insufficient_quota, etc.)
  - Exponential backoff on rate limits (HTTP 429) and server errors (HTTP 5xx)
  - Full resume support: skips already processed patient IDs
  - Immediate append-streaming to JSONL
  - Detached background execution mode (--detach)
  - Automatic evaluation summary generation upon completion

Usage:
  python scripts/run_hydrogen_ranking.py --detach
  python scripts/run_hydrogen_ranking.py --concurrency 3
"""

import argparse
import asyncio
import json
import math
import os
import random
import re
import subprocess
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

try:
    import httpx
except ImportError:
    sys.exit("httpx not installed. Run: pip install httpx")

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent

try:
    from dotenv import load_dotenv
    load_dotenv(REPO_ROOT / ".env")
    load_dotenv()
except ImportError:
    pass

# Ensure SSL_CERT_FILE points to a valid cert bundle
try:
    import certifi
    if "SSL_CERT_FILE" in os.environ and not os.path.exists(os.environ["SSL_CERT_FILE"]):
        os.environ["SSL_CERT_FILE"] = certifi.where()
    SSL_VERIFY = certifi.where() if os.path.exists(certifi.where()) else True
except ImportError:
    SSL_VERIFY = True

# Strip trailing parentheticals or annotations from predicted gene strings
_ID_IN_PARENS = re.compile(r'^([A-Za-z0-9\-_]+)(?:\s*\([^)]+\))?')
_CANDIDATE_LINE_RE = re.compile(r'^\s*([A-Za-z0-9\-_]+)\s*\((ENSG\d+|[A-Za-z0-9\-_]+)\)', re.M)

DEFAULT_MODEL = "google/gemini-4-hydrogen"

DEFAULT_PROMPTS = REPO_ROOT / "data" / "ranking" / "ranking_prompts.jsonl"
DEFAULT_ANSWERS = REPO_ROOT / "data" / "ranking" / "ranking_answers.jsonl"
DEFAULT_SAMPLE_FILE = REPO_ROOT / "results" / "ranking" / "Results_Claude_Sonnet_5_5" / "selected_500_patients.json"

DEFAULT_OUTPUT_DIR = REPO_ROOT / "results" / "ranking" / "Results_Gemini_4_Hydrogen"
DEFAULT_OUTPUT = DEFAULT_OUTPUT_DIR / "hydrogen_500_predictions.jsonl"

BROAD_CATEGORIES = [
    "known_gene_disease",
    "known_gene_known_disease",
    "known_gene_new_disease",
    "new_gene_known_disease",
    "new_gene_new_disease",
]


def log(msg: str):
    """Timestamped stdout logging."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] {msg}", flush=True)


def is_fatal_billing_error(status_code: int, text: str) -> bool:
    """Detect strict billing lockout (HTTP 402 or billing error markers)."""
    if status_code == 402:
        return True
    lower = text.lower()
    billing_markers = [
        "insufficient_quota", "out_of_credits", "billing account",
        "payment required", "exceeded your current quota", "billing_not_active"
    ]
    return any(m in lower for m in billing_markers)


def normalize_symbol(gene: str) -> str:
    """Extract and normalize HGNC symbol from string."""
    if not gene:
        return ""
    g = gene.strip()
    m = _ID_IN_PARENS.match(g)
    if m:
        g = m.group(1).strip()
    return g.upper()


def extract_gene_map_from_prompt(prompt: str) -> dict[str, str]:
    """Map Ensembl IDs and symbols observed in the Candidate Genes block to HGNC symbols."""
    mapping = {}
    for sym, ens in _CANDIDATE_LINE_RE.findall(prompt):
        s = sym.strip()
        e = ens.strip()
        if e.startswith("ENSG"):
            mapping[e] = s
        if s.startswith("ENSG"):
            mapping[s] = e
        mapping[s.upper()] = s
        mapping[e.upper()] = s
    return mapping


def load_dataset(prompts_path: Path, answers_path: Path, sample_file: Path | None = None, sample_size: int = 500, seed: int = 42) -> list[dict]:
    """Load canonical prompts, map answers, and select stratified sample or load from manifest."""
    answers = {}
    if answers_path.exists():
        with open(answers_path, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                ans = json.loads(line)
                answers[int(ans["id"])] = ans

    dataset_map = {}
    with open(prompts_path, "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            row = json.loads(line)
            pid = int(row["id"])
            prompt = row["prompt"]
            ans = answers.get(pid, {})

            true_ensg = ans.get("true_genes", [])
            gene_map = extract_gene_map_from_prompt(prompt)

            true_symbols = set()
            for ens in true_ensg:
                mapped = gene_map.get(ens) or gene_map.get(ens.upper())
                if mapped:
                    true_symbols.add(mapped.upper())

            for v in ans.get("variants", []):
                sym = v.get("gene_symbol")
                gid = v.get("gene_id")
                if sym and gid in true_ensg:
                    true_symbols.add(sym.upper())

            dataset_map[pid] = {
                "patient_id": pid,
                "disease_id": row.get("disease_id") or ans.get("disease_id"),
                "prompt": prompt,
                "category": ans.get("category", "unknown"),
                "broad_category": ans.get("broad_category", "unknown"),
                "in_gene_gene_kg": ans.get("in_gene_gene_kg", False),
                "in_disease_gene_kg": ans.get("in_disease_gene_kg", False),
                "true_genes": sorted(list(true_symbols)),
                "true_genes_ensg": true_ensg,
                "candidate_genes": [normalize_symbol(c) for c in ans.get("all_candidate_genes", [])],
                "gene_map": gene_map,
            }

    if sample_file and sample_file.exists():
        with open(sample_file, "r", encoding="utf-8") as f:
            manifest = json.load(f)
            target_ids = manifest.get("patient_ids", [])
            log(f"Loaded existing cohort sample manifest with {len(target_ids)} patients from {sample_file}")
            sample = [dataset_map[pid] for pid in target_ids if pid in dataset_map]
            return sample

    by_category = defaultdict(list)
    for p in dataset_map.values():
        by_category[p["broad_category"]].append(p)

    rng = random.Random(seed)
    per_cat_quota = sample_size // len(BROAD_CATEGORIES)
    sample = []
    for cat in BROAD_CATEGORIES:
        pool = sorted(by_category[cat], key=lambda x: x["patient_id"])
        chosen = rng.sample(pool, min(per_cat_quota, len(pool)))
        sample.extend(chosen)

    sample.sort(key=lambda x: x["patient_id"])
    return sample


def extract_predicted_genes(text: str) -> tuple[list[str], dict[str, float]]:
    """Parse JSON or fallback formatted list of prioritized genes from model response."""
    cleaned = text.strip()
    match = re.search(r'```(?:json)?\s*(\[\s*\{.*?\}\s*\])\s*```', cleaned, re.DOTALL)
    if not match:
        match = re.search(r'(\[\s*\{.*?\}\s*\])', cleaned, re.DOTALL)

    predicted_genes = []
    scores = {}

    if match:
        try:
            parsed = json.loads(match.group(1))
            if isinstance(parsed, list):
                for item in parsed:
                    if isinstance(item, dict):
                        gene = item.get("gene") or item.get("gene_symbol") or item.get("symbol")
                        score = item.get("score") or item.get("confidence") or item.get("priority")
                        if gene:
                            norm_g = normalize_symbol(str(gene))
                            if norm_g and norm_g not in predicted_genes:
                                predicted_genes.append(norm_g)
                                try:
                                    scores[norm_g] = float(score) if score is not None else 0.0
                                except (ValueError, TypeError):
                                    scores[norm_g] = 0.0
        except json.JSONDecodeError:
            pass

    if not predicted_genes:
        list_match = re.search(r'\[\s*("[^"]+"(?:\s*,\s*"[^"]+")*)\s*\]', cleaned)
        if list_match:
            try:
                raw_list = json.loads(f"[{list_match.group(1)}]")
                for g in raw_list:
                    norm_g = normalize_symbol(str(g))
                    if norm_g and norm_g not in predicted_genes:
                        predicted_genes.append(norm_g)
            except Exception:
                pass

    if not predicted_genes:
        for line in cleaned.splitlines():
            line_m = re.match(r'^\s*(?:\d+[\.\)]|\-|\*)\s*([A-Za-z0-9\-_]+)', line)
            if line_m:
                norm_g = normalize_symbol(line_m.group(1))
                if norm_g and norm_g not in ("GENE", "SCORE", "RANK") and norm_g not in predicted_genes:
                    predicted_genes.append(norm_g)

    return predicted_genes, scores


def score_patient_prediction(predicted_genes: list[str], true_symbols: list[str], true_ensg: list[str], gene_map: dict[str, str]) -> int | None:
    """Find 1-based rank of the true causal gene among predicted genes."""
    targets = set(s.upper() for s in true_symbols if s)
    for ens in true_ensg:
        targets.add(ens.upper())
        mapped = gene_map.get(ens) or gene_map.get(ens.upper())
        if mapped:
            targets.add(mapped.upper())

    for idx, raw_pred in enumerate(predicted_genes, 1):
        pred_norm = normalize_symbol(raw_pred)
        if pred_norm in targets:
            return idx
        mapped_pred = gene_map.get(pred_norm)
        if mapped_pred and mapped_pred.upper() in targets:
            return idx

    return None


async def call_aiva_chat_streaming(client: httpx.AsyncClient, api_url: str, prompt: str, model: str, headers: dict) -> str:
    """Call AIVA chat endpoint with SSE streaming."""
    data = {"message": prompt, "model": model, "stream": "false"}
    accumulated_text = []

    async with client.stream("POST", api_url, data=data, headers=headers, timeout=360.0) as resp:
        if is_fatal_billing_error(resp.status_code, ""):
            err_body = await resp.aread()
            log(f"FATAL BILLING ERROR: HTTP {resp.status_code} - {err_body.decode(errors='replace')[:300]}")
            raise SystemExit(f"Fatal billing lockout: HTTP {resp.status_code}")

        if resp.status_code != 200:
            err_body = await resp.aread()
            err_str = err_body.decode(errors="replace")
            if is_fatal_billing_error(resp.status_code, err_str):
                log(f"FATAL BILLING ERROR in body: {err_str[:300]}")
                raise SystemExit(f"Fatal billing error: {err_str[:200]}")
            raise httpx.HTTPStatusError(f"HTTP {resp.status_code}: {err_str[:150]}", request=resp.request, response=resp)

        async for line in resp.aiter_lines():
            line = line.strip()
            if not line:
                continue
            if line.startswith("data:"):
                payload = line[6:].strip()
                if payload == "[DONE]":
                    break
                try:
                    ev = json.loads(payload)
                    if isinstance(ev, dict):
                        if ev.get("type") == "error":
                            err_str = str(ev.get("error", ""))
                            if is_fatal_billing_error(0, err_str):
                                log(f"FATAL BILLING ERROR in stream: {err_str[:300]}")
                                raise SystemExit(f"Fatal billing error in stream: {err_str[:200]}")
                        if ev.get("type") in ("complete", "response_complete", "done"):
                            break
                        content = ev.get("content") or ev.get("delta") or ev.get("message")
                        if content and isinstance(content, str):
                            accumulated_text.append(content)
                        elif "response" in ev and isinstance(ev["response"], str):
                            accumulated_text.append(ev["response"])
                except json.JSONDecodeError:
                    accumulated_text.append(payload)

    raw_response = "".join(accumulated_text).strip()
    return raw_response


async def predict_single_patient(
    patient: dict,
    model: str,
    api_url: str,
    api_key: str,
    output_path: Path,
    file_lock: asyncio.Lock,
    semaphore: asyncio.Semaphore,
    max_retries: int = 5,
    dry_run: bool = False
) -> dict | None:
    """Prioritize genes for one simulated patient with exponential backoff and rate limit handling."""
    pid = patient["patient_id"]

    if dry_run:
        log(f"[DRY-RUN] Patient {pid}: Prompt length = {len(patient['prompt'])} chars")
        return None

    headers = {"Authorization": f"Bearer {api_key}"}

    async with semaphore:
        for attempt in range(1, max_retries + 1):
            try:
                t0 = time.time()
                async with httpx.AsyncClient(timeout=180.0, verify=SSL_VERIFY) as client:
                    raw_response = await call_aiva_chat_streaming(client, api_url, patient["prompt"], model, headers)
                elapsed = time.time() - t0

                predicted, scores = extract_predicted_genes(raw_response)
                rank = score_patient_prediction(predicted, patient["true_genes"], patient["true_genes_ensg"], patient["gene_map"])

                record = {
                    "patient_id": pid,
                    "disease_id": patient["disease_id"],
                    "ablation": "hpo+vcf+genes+disease",
                    "model": model,
                    "true_genes": patient["true_genes"],
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

                rank_str = f"Rank #{rank}" if rank is not None else "NOT FOUND"
                log(f"[DONE] Patient {pid:5d} ({patient['broad_category'][:20]:20s}) -> {rank_str:10s} (top 3: {predicted[:3]}) in {elapsed:.1f}s")

                async with file_lock:
                    output_path.parent.mkdir(parents=True, exist_ok=True)
                    with open(output_path, "a", encoding="utf-8") as f:
                        f.write(json.dumps(record) + "\n")
                        f.flush()

                return record

            except SystemExit:
                raise
            except httpx.HTTPStatusError as e:
                code = e.response.status_code
                if code == 429:
                    wait_time = 15 * (2 ** (attempt - 1)) + random.uniform(1, 4)
                    log(f"[RATE LIMIT 429] Patient {pid}: backoff {wait_time:.1f}s (attempt {attempt}/{max_retries})")
                    await asyncio.sleep(wait_time)
                elif code in (500, 502, 503, 504):
                    wait_time = 5 * (2 ** (attempt - 1)) + random.uniform(1, 3)
                    log(f"[SERVER ERROR {code}] Patient {pid}: backoff {wait_time:.1f}s (attempt {attempt}/{max_retries})")
                    await asyncio.sleep(wait_time)
                else:
                    log(f"[HTTP {code}] Patient {pid}: {str(e)[:120]}")
                    if attempt < max_retries:
                        await asyncio.sleep(5)
            except Exception as e:
                log(f"[EXCEPTION] Patient {pid}: {type(e).__name__}: {str(e)[:120]} (attempt {attempt}/{max_retries})")
                if attempt < max_retries:
                    await asyncio.sleep(5 * attempt)

        log(f"[FAIL] Patient {pid}: Exceeded max retries ({max_retries})")
        return None


async def run_benchmark(dataset: list[dict], output_path: Path, model: str, concurrency: int = 3, dry_run: bool = False) -> list[dict]:
    """Execute the full benchmark run with resuming and parallel tasks."""
    api_key = os.getenv("AIVA_API_KEY") or os.getenv("AIVA_API_TOKEN")
    if not api_key:
        sys.exit("Error: AIVA_API_KEY environment variable is missing.")

    api_url = os.getenv("AIVA_API_URL", "https://api.aivaportal.com/agents/chat/stream").rstrip("/")
    if "/agents/chat/stream" not in api_url:
        api_url = f"{api_url}/agents/chat/stream"

    completed_ids = set()
    if output_path.exists():
        with open(output_path, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                    completed_ids.add(int(row["patient_id"]))
                except Exception:
                    pass

    remaining = [p for p in dataset if p["patient_id"] not in completed_ids]
    log(f"Total cohort size: {len(dataset)} | Already completed: {len(completed_ids)} | Remaining to run: {len(remaining)}")

    if not remaining:
        log("All patients already completed. Proceeding directly to evaluation summary.")
        with open(output_path, "r", encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]

    file_lock = asyncio.Lock()
    semaphore = asyncio.Semaphore(concurrency)

    tasks = [
        predict_single_patient(
            p, model, api_url, api_key, output_path, file_lock, semaphore, dry_run=dry_run
        )
        for p in remaining
    ]

    await asyncio.gather(*tasks)

    results = []
    if output_path.exists():
        with open(output_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    results.append(json.loads(line))
    return results


def compute_benchmark_metrics(records: list[dict]) -> dict:
    """Calculate Recall@K, MRR, NDCG, and mean/median ranks across all and per category."""
    overall = []
    by_category = defaultdict(list)

    for r in records:
        rank = r.get("rank_of_true_gene")
        cat = r.get("broad_category", "unknown")
        overall.append(rank)
        by_category[cat].append(rank)

    def _calc(ranks: list) -> dict:
        n = len(ranks)
        if n == 0:
            return {}
        found = [r for r in ranks if r is not None and r > 0]
        rec1 = sum(1 for r in found if r <= 1) / n
        rec3 = sum(1 for r in found if r <= 3) / n
        rec5 = sum(1 for r in found if r <= 5) / n
        rec10 = sum(1 for r in found if r <= 10) / n
        rec20 = sum(1 for r in found if r <= 20) / n
        mrr = sum(1.0 / r for r in found) / n

        dcg10 = sum(1.0 / math.log2(r + 1) for r in found if r <= 10)
        ndcg10 = dcg10 / n

        mean_rank = sum(found) / len(found) if found else None
        median_rank = float(sorted(found)[len(found) // 2]) if found else None

        return {
            "n_patients": n,
            "retention_rate": round(len(found) / n, 4),
            "recall@1": round(rec1, 4),
            "recall@3": round(rec3, 4),
            "recall@5": round(rec5, 4),
            "recall@10": round(rec10, 4),
            "recall@20": round(rec20, 4),
            "mrr": round(mrr, 4),
            "ndcg10": round(ndcg10, 4),
            "mean_rank": round(mean_rank, 2) if mean_rank else None,
            "median_rank": median_rank,
        }

    metrics = {"overall": _calc(overall), "by_category": {}}
    for cat in BROAD_CATEGORIES:
        metrics["by_category"][cat] = _calc(by_category[cat])
    metrics.update(metrics["overall"])
    return metrics


def main():
    parser = argparse.ArgumentParser(description="Gene Prioritization Benchmark Runner for Gemini 4 Hydrogen")
    parser.add_argument("--model", type=str, default=DEFAULT_MODEL, help=f"Model identifier (default: {DEFAULT_MODEL})")
    parser.add_argument("--prompts", type=Path, default=DEFAULT_PROMPTS, help="Path to prompts JSONL")
    parser.add_argument("--answers", type=Path, default=DEFAULT_ANSWERS, help="Path to answers JSONL")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Output JSONL predictions path")
    parser.add_argument("--sample-file", type=Path, default=DEFAULT_SAMPLE_FILE, help="Path to pre-selected sample JSON manifest")
    parser.add_argument("--sample", type=int, default=500, help="Sample size (default 500)")
    parser.add_argument("--seed", type=int, default=42, help="Stratified random seed")
    parser.add_argument("--concurrency", type=int, default=3, help="Concurrent request limit")
    parser.add_argument("--detach", action="store_true", help="Launch detached background daemon")
    parser.add_argument("--dry-run", action="store_true", help="Print prompt preview without calling API")
    args = parser.parse_args()

    # If --detach is requested, spawn background process and exit
    if args.detach:
        output_dir = args.output.parent
        output_dir.mkdir(parents=True, exist_ok=True)
        log_path = output_dir / "hydrogen_ranking_run.log"
        pid_path = output_dir / "hydrogen_ranking_run.pid"

        cmd = [
            sys.executable, "-u", str(Path(__file__).resolve()),
            "--prompts", str(args.prompts.resolve()),
            "--answers", str(args.answers.resolve()),
            "--output", str(args.output.resolve()),
            "--model", args.model,
            "--sample", str(args.sample),
            "--seed", str(args.seed),
            "--concurrency", str(args.concurrency),
        ]
        if args.sample_file and args.sample_file.exists():
            cmd.extend(["--sample-file", str(args.sample_file.resolve())])

        with open(log_path, "a", encoding="utf-8") as log_file:
            log_file.write(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Detaching Gemini 4 Hydrogen background run (sample={args.sample}, seed={args.seed})\n")
            proc = subprocess.Popen(
                cmd,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )

        with open(pid_path, "w", encoding="utf-8") as f:
            f.write(str(proc.pid))

        print(f"Detached background process started with PID {proc.pid}")
        print(f"Log:    {log_path}")
        print(f"PID:    {pid_path}")
        print(f"Output: {args.output}")
        return

    log("=" * 70)
    log("STARTING GEMINI 4 HYDROGEN GENE PRIORITIZATION RUN")
    log(f"Model:       {args.model}")
    log(f"Ablation:    hpo+vcf+genes+disease (Canonical RareDxBench Prompts)")
    log(f"Prompts:     {args.prompts}")
    log(f"Answers:     {args.answers}")
    log(f"Output:      {args.output}")
    log(f"Sample File: {args.sample_file}")
    log(f"Sample:      {args.sample} patients")
    log(f"Seed:        {args.seed}")
    log(f"Concurrency: {args.concurrency}")
    log("=" * 70)

    dataset = load_dataset(args.prompts, args.answers, sample_file=args.sample_file, sample_size=args.sample, seed=args.seed)
    log(f"Loaded {len(dataset)} patients from dataset.")

    # Save selected patient IDs manifest for transparency
    manifest_path = args.output.parent / f"selected_{len(dataset)}_patients.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump({
            "sample_size": len(dataset),
            "seed": args.seed,
            "patient_ids": [p["patient_id"] for p in dataset],
            "categories": {cat: [p["patient_id"] for p in dataset if p["broad_category"] == cat] for cat in BROAD_CATEGORIES}
        }, f, indent=2)
    log(f"Saved patient sampling manifest to {manifest_path}.")

    results = asyncio.run(run_benchmark(dataset, args.output, args.model, concurrency=args.concurrency, dry_run=args.dry_run))

    if not args.dry_run and results:
        log("\nComputing aggregate metrics...")
        metrics = compute_benchmark_metrics(results)
        summary_path = args.output.parent / f"{args.output.stem}_evaluation_summary.json"
        summary_payload = {
            "model": args.model,
            "ablation": "hpo+vcf+genes+disease",
            "n_evaluated": len(results),
            "sample_stratified": True,
            "metrics": metrics,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        with open(summary_path, "w", encoding="utf-8") as f:
            json.dump(summary_payload, f, indent=2)

        log("\n" + "=" * 70)
        log("GENE PRIORITIZATION BENCHMARK RESULTS (GEMINI 4 HYDROGEN)")
        log("=" * 70)
        log(f"Patients Evaluated : {metrics.get('n_patients')}")
        log(f"Retention Rate     : {metrics.get('retention_rate', 0):.1%}")
        log(f"Recall@1           : {metrics.get('recall@1', 0):.1%}")
        log(f"Recall@5           : {metrics.get('recall@5', 0):.1%}")
        log(f"Recall@10          : {metrics.get('recall@10', 0):.1%}")
        log(f"Recall@20          : {metrics.get('recall@20', 0):.1%}")
        log(f"MRR                : {metrics.get('mrr')}")
        log(f"NDCG@10            : {metrics.get('ndcg@10')}")
        log(f"Mean Rank          : {metrics.get('mean_rank')}")
        log(f"Median Rank        : {metrics.get('median_rank')}")
        log(f"Evaluation summary saved to: {summary_path}")
        log("=" * 70)


if __name__ == "__main__":
    main()
