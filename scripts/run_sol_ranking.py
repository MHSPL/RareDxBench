#!/usr/bin/env python3
"""
Gene Prioritization Benchmark Runner for GPT-6.1 Sol
====================================================
Runs GPT-6.1 Sol (openai/gpt-6.1-sol) on the canonical 500-sample
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
  python scripts/run_sol_ranking.py --detach
  python scripts/run_sol_ranking.py --concurrency 3
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

DEFAULT_MODEL = "openai/gpt-6.1-sol"

DEFAULT_PROMPTS = REPO_ROOT / "data" / "ranking" / "ranking_prompts.jsonl"
DEFAULT_ANSWERS = REPO_ROOT / "data" / "ranking" / "ranking_answers.jsonl"
DEFAULT_SAMPLE_FILE = REPO_ROOT / "results" / "ranking" / "Results_Claude_Sonnet_5_5" / "selected_500_patients.json"

DEFAULT_OUTPUT_DIR = REPO_ROOT / "results" / "ranking" / "Results_GPT_6_1_Sol"
DEFAULT_OUTPUT = DEFAULT_OUTPUT_DIR / "sol_500_predictions.jsonl"

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

            # Map from prompt
            gene_map = extract_gene_map_from_prompt(prompt)

            # Resolve true genes
            true_ensg_list = ans.get("true_genes", [])
            true_symbols = [gene_map.get(eg, eg) for eg in true_ensg_list]
            bc = ans.get("broad_category", "unknown")

            item = {
                "patient_id": pid,
                "prompt": prompt,
                "disease_id": ans.get("disease_id", ""),
                "true_genes_ensg": true_ensg_list,
                "true_genes": true_symbols,
                "category": ans.get("category", "unknown"),
                "broad_category": bc,
                "in_gene_gene_kg": ans.get("in_gene_gene_kg"),
                "in_disease_gene_kg": ans.get("in_disease_gene_kg"),
                "all_candidate_genes": ans.get("all_candidate_genes", []),
            }
            dataset_map[pid] = item

    # If sample_file is specified and exists, load exact patient IDs
    if sample_file and Path(sample_file).exists():
        with open(sample_file, "r", encoding="utf-8") as f:
            manifest = json.load(f)
        manifest_pids = manifest.get("patient_ids", [])
        selected = [dataset_map[pid] for pid in manifest_pids if pid in dataset_map]
        log(f"Loaded {len(selected)} patients directly from sample manifest: {sample_file}")
        return selected

    by_category = defaultdict(list)
    for item in dataset_map.values():
        by_category[item["broad_category"]].append(item)

    if sample_size <= 0 or sample_size >= len(dataset_map):
        all_items = []
        for cat in BROAD_CATEGORIES:
            all_items.extend(by_category.get(cat, []))
        return all_items

    rng = random.Random(seed)
    n_cats = len(BROAD_CATEGORIES)
    per_cat_target = sample_size // n_cats
    remainder = sample_size % n_cats

    selected = []
    log(f"Stratified sampling ({sample_size} total, ~{per_cat_target} per category, seed={seed}):")
    for i, cat in enumerate(BROAD_CATEGORIES):
        cat_items = by_category.get(cat, [])
        quota = per_cat_target + (1 if i < remainder else 0)
        actual_take = min(len(cat_items), quota)
        cat_sample = rng.sample(cat_items, actual_take)
        selected.extend(cat_sample)
        log(f"  • {cat:26s}: {actual_take}/{len(cat_items)} selected")

    selected.sort(key=lambda x: x["patient_id"])
    return selected


async def call_aiva(client: httpx.AsyncClient, api_url: str, api_key: str, model: str, prompt: str, max_retries: int = 5) -> str | None:
    headers = {"Authorization": f"Bearer {api_key}"}
    data = {"message": prompt, "model": model, "stream": "false"}

    for attempt in range(1, max_retries + 1):
        try:
            async with client.stream("POST", api_url, data=data, headers=headers, timeout=180.0) as resp:
                if resp.status_code == 429:
                    delay = 15 * attempt
                    log(f"  [429 Rate Limit] Backing off {delay}s (attempt {attempt}/{max_retries})...")
                    await asyncio.sleep(delay)
                    continue

                if is_fatal_billing_error(resp.status_code, ""):
                    body = (await resp.aread()).decode("utf-8", errors="replace")
                    log(f"FATAL BILLING ERROR (HTTP {resp.status_code}): {body[:300]}")
                    raise SystemExit(f"Fatal billing error (HTTP {resp.status_code}): {body[:200]}")

                if 500 <= resp.status_code < 600:
                    delay = 5 * attempt
                    log(f"  [HTTP {resp.status_code}] Retrying in {delay}s (attempt {attempt}/{max_retries})...")
                    await asyncio.sleep(delay)
                    continue

                if resp.status_code != 200:
                    body = (await resp.aread()).decode("utf-8", errors="replace")
                    if is_fatal_billing_error(resp.status_code, body):
                        log(f"FATAL BILLING ERROR (HTTP {resp.status_code}): {body[:300]}")
                        raise SystemExit(f"Fatal billing error (HTTP {resp.status_code}): {body[:200]}")
                    log(f"  API error {resp.status_code}: {body[:300]}")
                    return None

                text = ""
                async for line in resp.aiter_lines():
                    line = line.strip()
                    if not line or not line.startswith("data: "):
                        continue
                    payload = line[6:]
                    if payload == "[DONE]":
                        break
                    try:
                        ev = json.loads(payload)
                        if ev.get("error"):
                            err_str = str(ev.get("error"))
                            if is_fatal_billing_error(resp.status_code, err_str):
                                log(f"FATAL BILLING ERROR in stream: {err_str[:300]}")
                                raise SystemExit(f"Fatal billing error in stream: {err_str[:200]}")
                        if ev.get("type") in ("text_delta", "final_output"):
                            text += ev.get("content", "")
                    except json.JSONDecodeError:
                        pass
                return text

        except SystemExit:
            raise
        except Exception as exc:
            if attempt == max_retries:
                log(f"  Request failed after {max_retries} attempts: {exc}")
                return None
            await asyncio.sleep(5 * attempt)

    return None


def parse_predictions(response_text: str) -> tuple[list[str], dict[str, float]]:
    """Parse JSON response containing predicted_genes list and confidence scores."""
    genes: list[str] = []
    scores: dict[str, float] = {}

    decoder = json.JSONDecoder()
    idx = 0
    while idx < len(response_text):
        if response_text[idx] != "{":
            idx += 1
            continue
        try:
            parsed, span = decoder.raw_decode(response_text[idx:])
        except json.JSONDecodeError:
            idx += 1
            continue

        if isinstance(parsed, dict) and "predicted_genes" in parsed:
            pg = parsed["predicted_genes"]
            if isinstance(pg, list):
                for entry in pg:
                    if isinstance(entry, dict):
                        gene = entry.get("gene") or entry.get("symbol") or entry.get("name")
                        scr = entry.get("score")
                        if gene:
                            sym = normalize_symbol(str(gene))
                            genes.append(sym)
                            if scr is not None:
                                try:
                                    scores[sym] = float(scr)
                                except (ValueError, TypeError):
                                    pass
                    elif isinstance(entry, str):
                        genes.append(normalize_symbol(entry))
            break
        idx += max(span, 1)

    # De-duplicate preserving ranking order
    seen = set()
    deduped = []
    for g in genes:
        if g and g not in seen:
            seen.add(g)
            deduped.append(g)

    return deduped, scores


def find_rank(predicted: list[str], true_genes: list[str]) -> int | None:
    """Return 1-based rank of the true gene in predicted list."""
    true_set = {normalize_symbol(g) for g in true_genes if g}
    for i, g in enumerate(predicted, 1):
        if normalize_symbol(g) in true_set:
            return i
    return None


def compute_benchmark_metrics(results: list[dict], at_ks: tuple[int, ...] = (1, 5, 10, 20, 50)) -> dict:
    """Compute standard gene ranking benchmark metrics."""
    ranks = []
    found_at = {k: 0 for k in at_ks}
    reciprocal_ranks = []
    dcg_list = []
    no_prediction = 0
    retention_count = 0

    per_category = defaultdict(list)

    for r in results:
        predicted = r.get("predicted", [])
        true_genes = r.get("true_genes", [])
        cat = r.get("broad_category", r.get("category", "unknown"))

        if not predicted:
            no_prediction += 1
            per_category[cat].append(None)
            reciprocal_ranks.append(0.0)
            dcg_list.append(0.0)
            continue

        rk = find_rank(predicted, true_genes)
        per_category[cat].append(rk)

        if rk is not None:
            retention_count += 1
            ranks.append(rk)
            reciprocal_ranks.append(1.0 / rk)
            dcg_list.append(1.0 / math.log2(rk + 1) if rk <= 10 else 0.0)
            for k in at_ks:
                if rk <= k:
                    found_at[k] += 1
        else:
            reciprocal_ranks.append(0.0)
            dcg_list.append(0.0)

    n = len(results)
    if n == 0:
        return {}

    idcg = 1.0
    metrics = {
        "n_patients": n,
        "no_prediction": no_prediction,
        "retention_rate": round(retention_count / n, 4),
        "mrr": round(sum(reciprocal_ranks) / n, 4),
        "ndcg@10": round(sum(dcg_list) / (n * idcg), 4),
        "mean_rank": round(sum(ranks) / len(ranks), 2) if ranks else None,
        "median_rank": float(sorted(ranks)[len(ranks) // 2]) if ranks else None,
    }
    for k in at_ks:
        metrics[f"recall@{k}"] = round(found_at[k] / n, 4)

    cat_metrics = {}
    for cat, cat_ranks in per_category.items():
        n_cat = len(cat_ranks)
        cm = {"n": n_cat}
        for k in at_ks:
            found_k = sum(1 for rk in cat_ranks if rk is not None and rk <= k)
            cm[f"recall@{k}"] = round(found_k / n_cat, 4)
        cat_metrics[cat] = cm
    metrics["per_category"] = cat_metrics

    return metrics


async def run_benchmark(dataset: list[dict], output_path: Path, model: str, concurrency: int = 3, dry_run: bool = False):
    api_url = os.getenv("AIVA_API_URL", "https://api.aivaportal.com/agents/chat/stream").rstrip("/")
    if "/agents/chat/stream" not in api_url:
        api_url = f"{api_url}/agents/chat/stream"
    api_key = os.getenv("AIVA_API_KEY") or os.getenv("AIVA_API_TOKEN", "")

    if not api_key:
        sys.exit("ERROR: AIVA_API_TOKEN / AIVA_API_KEY is not set.")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Resume support
    done_ids = set()
    results = []
    if output_path.exists():
        with open(output_path, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                    done_ids.add(int(r["patient_id"]))
                    results.append(r)
                except Exception:
                    pass
        if done_ids:
            log(f"Resuming run: {len(done_ids)} patients already completed.")

    remaining = [p for p in dataset if p["patient_id"] not in done_ids]
    log(f"Patients to process: {len(remaining)} / {len(dataset)}")
    log(f"Model: {model}")
    log(f"Concurrency: {concurrency}")

    if not remaining:
        log("All requested patients are already completed.")
        return results

    sem = asyncio.Semaphore(concurrency)
    counter = {"done": 0}
    total = len(remaining)

    async with httpx.AsyncClient(verify=SSL_VERIFY) as client:
        with open(output_path, "a", encoding="utf-8") as fout:
            async def process_patient(patient: dict):
                pid = patient["patient_id"]
                prompt = patient["prompt"]
                true_genes = patient["true_genes"]

                if dry_run:
                    log(f"\n[Dry Run] Patient {pid}:")
                    print(prompt[:400] + "...\n")
                    return None

                async with sem:
                    resp_text = await call_aiva(client, api_url, api_key, model, prompt)

                predicted, scores = parse_predictions(resp_text or "")
                rank = find_rank(predicted, true_genes)

                rec = {
                    "patient_id": pid,
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

                counter["done"] += 1
                top_gene = predicted[0] if predicted else "NONE"
                top_score = scores.get(top_gene, 0.0)
                true_gene_str = true_genes[0] if true_genes else "UNKNOWN"
                true_score = scores.get(true_gene_str, 0.0)

                status = f"RANK_{rank}" if rank is not None else "MISSED"
                if rank == 1:
                    status = "✓ RANK_1"

                in_model = "✓" if rank is not None else "✗"
                log(f"  [{counter['done']}/{total}] Patient {pid} ({patient['broad_category']}): Top={top_gene}(s={top_score:.2f}) | "
                    f"True={true_gene_str}(rank={rank}, s={true_score:.2f}, in_model={in_model}) | {status}")

                fout.write(json.dumps(rec, ensure_ascii=False) + "\n")
                fout.flush()
                results.append(rec)
                return rec

            tasks = [process_patient(p) for p in remaining]
            await asyncio.gather(*tasks)

    return results


def main():
    parser = argparse.ArgumentParser(description="Run GPT-6.1 Sol on Gene Prioritization Benchmark")
    parser.add_argument("--prompts", type=Path, default=DEFAULT_PROMPTS, help="Path to ranking_prompts.jsonl")
    parser.add_argument("--answers", type=Path, default=DEFAULT_ANSWERS, help="Path to ranking_answers.jsonl")
    parser.add_argument("--sample-file", type=Path, default=DEFAULT_SAMPLE_FILE, help="Path to selected patients JSON manifest")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Path to output predictions JSONL")
    parser.add_argument("--model", type=str, default=DEFAULT_MODEL, help="Model identifier")
    parser.add_argument("--sample", type=int, default=500, help="Number of patients to evaluate")
    parser.add_argument("--seed", type=int, default=42, help="Random seed for category-stratified sampling")
    parser.add_argument("--concurrency", type=int, default=3, help="Concurrent request limit")
    parser.add_argument("--detach", action="store_true", help="Launch detached background daemon")
    parser.add_argument("--dry-run", action="store_true", help="Print prompt preview without calling API")
    args = parser.parse_args()

    # If --detach is requested, spawn background process and exit
    if args.detach:
        output_dir = args.output.parent
        output_dir.mkdir(parents=True, exist_ok=True)
        log_path = output_dir / "sol_ranking_run.log"
        pid_path = output_dir / "sol_ranking_run.pid"

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
            log_file.write(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Detaching GPT-6.1 Sol background run (sample={args.sample}, seed={args.seed})\n")
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
    log("STARTING GPT-6.1 SOL GENE PRIORITIZATION RUN")
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
        log("GENE PRIORITIZATION BENCHMARK RESULTS (GPT-6.1 SOL)")
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
