#!/usr/bin/env python3
"""
Re-run unclassified variants for GPT-6 Astra (openai/gpt-6-astra).

Features:
- Targeted re-evaluation of unclassified variants.
- Multi-phase resolution: if standard payload yields 'Not classified' due to
  conflicting HGVS vs genomic coordinates or transcript lookup issues,
  automatically retries with reconciled representations (HGVS-only, coords-only,
  or canonical transcript).
- Automatically appends/merges newly resolved classifications into
  results/classification/Results_GPT_6_Astra_Analysis/astra_full.jsonl.
- Automatically refreshes the dashboard (scripts/update_dashboard.py).

Usage:
    python scripts/run_astra_unclassified.py --input FILE [--concurrency 2]

FILE is a JSONL of the variants to re-run, in the format of
data/classification/aiva_bench_input.jsonl (default:
data/classification/astra_unclassified_variants.jsonl, which a one-off
reconciliation produced and is not kept in the repo).
"""

import argparse
import asyncio
import json
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import httpx

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from config import (
    AIVA_API_URL,
    AIVA_API_TOKEN,
    DEFAULT_ASSEMBLY,
    CLASSIFICATION_TYPE,
    CLASSIFICATION_ANSWERS,
    CLASSIFICATION_DATA_DIR,
    CLASSIFICATION_RESULTS_DIR,
)
from scripts.classify import get_variant_data
from scripts.overnight_runner import clean_hgvs

RESULTS_DIR = CLASSIFICATION_RESULTS_DIR / "Results_GPT_6_Astra_Analysis"
DEFAULT_INPUT = CLASSIFICATION_DATA_DIR / "astra_unclassified_variants.jsonl"
OUTPUT_FILE = RESULTS_DIR / "astra_unclassified_results.jsonl"
FULL_FILE = RESULTS_DIR / "astra_full.jsonl"
BENCHMARK_META = CLASSIFICATION_ANSWERS

MODEL_NAME = "openai/gpt-6-astra"
REQUEST_TIMEOUT = 180.0


def log(msg: str):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] {msg}", flush=True)


def load_ground_truth_map() -> Dict[str, Tuple[Optional[str], list, Optional[str]]]:
    """Load ground truth classification, criteria, and panel from benchmark meta."""
    gt_map = {}
    if BENCHMARK_META.exists():
        with open(BENCHMARK_META, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line)
                    vid = rec.get("id") or rec.get("variant_id")
                    if vid:
                        gt_map[vid] = (
                            rec.get("classification"),
                            rec.get("criteria_met") or [],
                            rec.get("expert_panel"),
                        )
                except Exception:
                    continue
    return gt_map


def extract_coords(variant_key: str) -> Optional[dict]:
    if variant_key and ":" in variant_key:
        parts = variant_key.split(":")
        if len(parts) == 4:
            return {
                "chromosome": parts[0].replace("chr", ""),
                "position": int(parts[1]),
                "ref": parts[2],
                "alt": parts[3],
            }
    return None


def generate_payload_variants(variant: dict) -> List[Tuple[str, dict]]:
    """
    Generate ordered representations to try:
    1. 'standard': cleaned HGVS + genomic coords (default)
    2. 'hgvs_only': cleaned HGVS without coords (resolves coordinate conflicts)
    3. 'coords_only': genomic coords without HGVS (resolves transcript version conflicts)
    4. 'canonical_transcript': HGVS with version suffix adjusted (e.g. .1/.2 -> .3/.5)
    """
    raw_var = variant.get("variation") or variant.get("variation_original") or ""
    cleaned = clean_hgvs(raw_var)
    coords = extract_coords(variant.get("variant_key", ""))

    payloads = []

    # 1. Standard
    res_std = {}
    if cleaned and (":c." in cleaned or ":g." in cleaned or ":n." in cleaned) and "(" not in cleaned:
        res_std["hgvs"] = cleaned
    if coords:
        res_std.update(coords)
    if not res_std:
        res_std = get_variant_data(variant)
    payloads.append(("standard", res_std))

    # 2. HGVS only (if coords present)
    if cleaned and coords:
        payloads.append(("hgvs_only", {"hgvs": cleaned}))

    # 3. Coords only (if cleaned HGVS present)
    if coords and cleaned:
        payloads.append(("coords_only", dict(coords)))

    # 4. Canonical transcript version adjustment
    if cleaned:
        bumped = re.sub(r"NM_000277\.[12]", "NM_000277.3", cleaned)
        bumped = re.sub(r"NM_004360\.4", "NM_004360.5", bumped)
        if bumped != cleaned:
            p = {"hgvs": bumped}
            if coords:
                p.update(coords)
            payloads.append(("canonical_transcript", p))

    return payloads


async def call_api(
    client: httpx.AsyncClient,
    variant_data: dict,
    variant: dict,
    max_retries: int = 4
) -> Tuple[dict, int]:
    gene_symbol = variant.get("gene_symbol")
    disease = variant.get("disease")
    vid = (variant.get("id") or variant.get("variant_id") or "")[:8]

    payload = {
        "variant_data": variant_data,
        "classification_type": CLASSIFICATION_TYPE,
        "assembly": variant.get("genome_build") or DEFAULT_ASSEMBLY,
        "model": MODEL_NAME,
        "phenotype_terms": disease,
        "description": f"Gene: {gene_symbol}",
        "additional_context": f"Mode of inheritance: {variant.get('mode_of_inheritance', 'Unknown')}"
    }

    for attempt in range(max_retries + 1):
        t0 = time.time()
        try:
            resp = await client.post(
                "/api/analyze/classify-variant",
                json=payload,
                timeout=httpx.Timeout(REQUEST_TIMEOUT)
            )
            elapsed_ms = int((time.time() - t0) * 1000)

            if resp.status_code == 200:
                data = resp.json()
                return data.get("data", data), elapsed_ms

            if resp.status_code == 429:
                backoff = 30 * (attempt + 1)
                log(f"  [429 Rate Limit for {gene_symbol} ({vid})] Retrying in {backoff}s...")
                await asyncio.sleep(backoff)
                continue

            if resp.status_code in (502, 503, 504):
                backoff = 10 * (2 ** attempt)
                log(f"  [HTTP {resp.status_code} for {gene_symbol} ({vid})] Retrying in {backoff}s...")
                await asyncio.sleep(backoff)
                continue

            if resp.status_code == 500:
                log(f"  [HTTP 500 for {gene_symbol} ({vid})]: {resp.text[:150]}")
                return {"classification": "Error", "error": f"HTTP 500: {resp.text[:200]}"}, elapsed_ms

            log(f"  [HTTP {resp.status_code} for {gene_symbol} ({vid})]: {resp.text[:150]}")
            return {"classification": "Error", "error": f"HTTP {resp.status_code}: {resp.text[:200]}"}, elapsed_ms

        except (httpx.TimeoutException, httpx.NetworkError) as e:
            elapsed_ms = int((time.time() - t0) * 1000)
            if attempt < max_retries:
                backoff = 15 * (attempt + 1)
                log(f"  [{type(e).__name__} for {gene_symbol} ({vid})] Retrying in {backoff}s...")
                await asyncio.sleep(backoff)
            else:
                return {"classification": "Error", "error": f"Network/Timeout: {str(e)}"}, elapsed_ms

    return {"classification": "Error", "error": "Max retries exceeded"}, 0


def build_final_record(variant: dict, cls_data: dict, gt_map: dict, elapsed_ms: int) -> dict:
    vid = variant.get("id") or variant.get("variant_id")
    gt_clf, gt_crit, panel = gt_map.get(vid, (None, [], variant.get("expert_panel")))

    return {
        "variant_id": vid,
        "variant": variant.get("variation") or variant.get("variation_original"),
        "gene": variant.get("gene_symbol"),
        "disease": variant.get("disease"),
        "ground_truth": gt_clf or variant.get("ground_truth"),
        "ground_truth_criteria": gt_crit or variant.get("ground_truth_criteria", []),
        "expert_panel": panel or variant.get("expert_panel"),
        "execution_time_ms": elapsed_ms,
        "model_used": MODEL_NAME,
        "aiva_classification": cls_data.get("classification"),
        "aiva_confidence": cls_data.get("confidence"),
        "aiva_criteria": cls_data.get("criteria_met", []),
        "aiva_evidence": cls_data.get("evidence_summary", {}),
        "aiva_acmg_score": cls_data.get("acmg_score"),
        "aiva_rationale": cls_data.get("classification_rationale"),
        "aiva_input_issue": cls_data.get("input_issue", ""),
        "error": cls_data.get("error"),
    }


async def evaluate_variant(
    client: httpx.AsyncClient,
    variant: dict,
    gt_map: dict
) -> dict:
    vid = (variant.get("id") or variant.get("variant_id") or "")[:8]
    gene = variant.get("gene_symbol")
    payloads = generate_payload_variants(variant)

    last_record = None
    for mode, var_payload in payloads:
        cls_data, elapsed_ms = await call_api(client, var_payload, variant)
        clf = cls_data.get("classification")
        last_record = build_final_record(variant, cls_data, gt_map, elapsed_ms)

        if clf and clf not in ("Not classified", "Error"):
            log(f"  ✓ {gene} ({vid}): Classified as '{clf}' via {mode} ({elapsed_ms}ms)")
            return last_record
        else:
            issue = cls_data.get("input_issue") or cls_data.get("classification_rationale") or cls_data.get("error") or ""
            log(f"  • {gene} ({vid}): [{mode}] returned '{clf}': {issue[:90]}")
            # Brief pause before trying fallback mode
            await asyncio.sleep(2)

    return last_record


def update_full_file(new_records: List[dict]):
    """Update or append new successful records in results/classification/Results_GPT_6_Astra_Analysis/astra_full.jsonl."""
    if not FULL_FILE.exists():
        return

    existing_lines = []
    with open(FULL_FILE, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                try:
                    existing_lines.append(json.loads(line))
                except Exception:
                    continue

    by_id = {}
    for r in existing_lines:
        vid = r.get("variant_id") or r.get("id")
        if vid:
            by_id[vid] = r

    updated_count = 0
    for r in new_records:
        vid = r.get("variant_id") or r.get("id")
        clf = r.get("aiva_classification")
        if vid and clf and clf not in ("Not classified", "Error"):
            by_id[vid] = r
            updated_count += 1

    temp_file = FULL_FILE.with_suffix(".tmp")
    with open(temp_file, "w", encoding="utf-8") as f:
        for r in by_id.values():
            f.write(json.dumps(r) + "\n")
    temp_file.replace(FULL_FILE)

    log(f"Merged {updated_count} newly classified variants into {FULL_FILE.name} (total unique variants: {len(by_id)})")


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--concurrency", type=int, default=2)
    args = parser.parse_args()

    if not args.input.exists():
        sys.exit(f"Input file not found: {args.input}")
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    variants = []
    with open(args.input, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                variants.append(json.loads(line))

    log(f"Starting Astra Re-evaluation Job for {len(variants)} unclassified variants (Concurrency: {args.concurrency})")
    gt_map = load_ground_truth_map()

    headers = {
        "Authorization": f"Bearer {AIVA_API_TOKEN}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    sem = asyncio.Semaphore(args.concurrency)
    results = []
    completed = 0
    total = len(variants)

    async with httpx.AsyncClient(base_url=AIVA_API_URL, headers=headers) as client:
        async def worker(v):
            nonlocal completed
            async with sem:
                rec = await evaluate_variant(client, v, gt_map)
                completed += 1
                clf = rec.get("aiva_classification")
                vid = (rec.get("variant_id") or "")[:8]
                log(f"[{completed}/{total}] Finished {rec.get('gene')} ({vid}): {clf}")
                with open(OUTPUT_FILE, "a", encoding="utf-8") as out:
                    out.write(json.dumps(rec) + "\n")
                # Inter-call pacing to respect rate limit
                await asyncio.sleep(2)
                return rec

        tasks = [worker(v) for v in variants]
        results = await asyncio.gather(*tasks)

    classified_count = sum(
        1 for r in results if r.get("aiva_classification") not in ("Not classified", "Error", None)
    )
    log(f"Re-run complete: {classified_count}/{total} variants successfully classified!")

    # Merge into astra_full.jsonl
    update_full_file(results)

    # Refresh dashboard
    log("Refreshing dashboard data and HTML...")
    update_script = REPO_ROOT / "scripts" / "update_dashboard.py"
    res = subprocess.run([sys.executable, str(update_script)], check=False).returncode
    if res == 0:
        log("Dashboard refreshed successfully!")
    else:
        log(f"Dashboard update returned exit code {res}")


if __name__ == "__main__":
    asyncio.run(main())
