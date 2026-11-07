#!/usr/bin/env python3
"""
Variant Classification Runner for GPT-6 Astra (openai/gpt-6-astra)
on the RareDx Bench benchmark cohort (2,894 ClinGen variants).

Features:
- Single self-contained run script with concurrency control (default: 5).
- Integrated multi-phase variant resolution: automatically handles HGVS-only,
  coords-only, and canonical transcript fallbacks for variants that return
  'Not classified' under standard representations.
- Full resume support: skips variants already classified in the output JSONL.
- Multi-pass sweep: sweeps through any pending or transiently errored variants.
- Real-time streaming: appends results to the output file as each variant finishes.

Usage:
    python3 scripts/run_astra.py
    python3 scripts/run_astra.py --concurrency 5 --max-passes 4
"""

import argparse
import asyncio
import json
import os
import re
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
    CLASSIFICATION_ANSWERS,
    CLASSIFICATION_RESULTS_DIR,
    DEFAULT_ASSEMBLY,
    CLASSIFICATION_TYPE,
)

MODEL_NAME = "openai/gpt-6-astra"
OUTPUT_DIR = CLASSIFICATION_RESULTS_DIR / "Results_GPT_6_Astra_Analysis"
OUTPUT_FILE = OUTPUT_DIR / "astra_full.jsonl"
DEFAULT_INPUT = CLASSIFICATION_ANSWERS

REQUEST_TIMEOUT = 300.0
MAX_RETRIES = 4
RATE_LIMIT_DELAY = 25.0


def log(msg: str):
    """Timestamped stdout logging."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] {msg}", flush=True)


def clean_hgvs(raw_var: str) -> str:
    """Strip protein change and inner gene parentheses from HGVS strings."""
    cleaned = re.sub(r"\s*\(p\.[^\)]+\)", "", raw_var).strip()
    cleaned = re.sub(r"\(.*?\):", ":", cleaned)
    return cleaned


def extract_coords(variant_key: str) -> Optional[dict]:
    """Parse chr:pos:ref:alt into coordinate dict."""
    if variant_key and ":" in variant_key:
        parts = variant_key.split(":")
        if len(parts) == 4:
            return {
                "chromosome": parts[0].replace("chr", ""),
                "position": int(parts[1]),
                "ref": parts[2],
                "alt": parts[3]
            }
    return None


def generate_payload_variants(variant: dict) -> List[Tuple[str, dict]]:
    """
    Generate ordered representations to try:
    1. 'standard': cleaned HGVS + genomic coords (default)
    2. 'hgvs_only': cleaned HGVS without coords (resolves coordinate conflicts)
    3. 'coords_only': genomic coords without HGVS (resolves transcript version conflicts)
    4. 'canonical_transcript': HGVS with canonical version bump (e.g. .1/.2 -> .3/.5)
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
    if res_std:
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


def is_fatal_billing_error(status_code: int, text: str) -> bool:
    """Detect strict billing lockouts (HTTP 402)."""
    if status_code == 402:
        return True
    lower = text.lower()
    billing_markers = [
        "insufficient_quota", "out_of_credits", "billing account",
        "payment required", "exceeded your current quota"
    ]
    return any(m in lower for m in billing_markers)


def load_classified_variant_ids(output_path: Path) -> Set[str]:
    """Return set of variant IDs that have been successfully classified."""
    if not output_path.exists():
        return set()
    classified = set()
    with open(output_path, "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            vid = rec.get("variant_id") or rec.get("id")
            cls = rec.get("aiva_classification")
            err = rec.get("error")
            if vid and not err and cls and cls != "Not classified":
                classified.add(vid)
    return classified


async def call_api(
    client: httpx.AsyncClient,
    var_payload: dict,
    variant: dict,
    model: str = MODEL_NAME
) -> Tuple[dict, int]:
    """Call AIVA classify-variant endpoint with retries."""
    req_body = {
        "variant_data": var_payload,
        "classification_type": CLASSIFICATION_TYPE,
        "assembly": DEFAULT_ASSEMBLY,
        "model": model,
        "phenotype_terms": variant.get("disease"),
        "description": f"Gene: {variant.get('gene_symbol', 'Unknown')}",
        "additional_context": f"Mode of inheritance: {variant.get('mode_of_inheritance', 'Unknown')}"
    }

    start = time.time()
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = await client.post("/api/analyze/classify-variant", json=req_body)
            elapsed_ms = int((time.time() - start) * 1000)

            if resp.status_code == 200:
                data = resp.json()
                return data.get("data", data), elapsed_ms

            if is_fatal_billing_error(resp.status_code, resp.text):
                raise RuntimeError(f"Fatal billing error (HTTP {resp.status_code}): {resp.text[:200]}")

            if resp.status_code == 429:
                delay = RATE_LIMIT_DELAY * attempt
                log(f"  [429 Rate Limit] Backing off {delay}s (attempt {attempt}/{MAX_RETRIES})...")
                await asyncio.sleep(delay)
                continue

            if 500 <= resp.status_code < 600:
                log(f"  [HTTP {resp.status_code}] Retrying in 10s (attempt {attempt}/{MAX_RETRIES})...")
                await asyncio.sleep(10)
                continue

            return {"error": f"HTTP {resp.status_code}: {resp.text[:200]}"}, elapsed_ms

        except (httpx.ConnectError, httpx.ReadTimeout, httpx.WriteTimeout) as net_err:
            if attempt == MAX_RETRIES:
                return {"error": f"Network error after {MAX_RETRIES} attempts: {net_err}"}, int((time.time() - start) * 1000)
            await asyncio.sleep(5 * attempt)

    return {"error": "Exceeded max retries"}, int((time.time() - start) * 1000)


def build_result_record(variant: dict, cls_data: dict, elapsed_ms: int) -> dict:
    """Format final output row."""
    vid = variant.get("id") or variant.get("variant_id")
    gene = variant.get("gene_symbol") or variant.get("gene") or "Unknown"
    return {
        "variant_id": vid,
        "variant": variant.get("variation") or variant.get("variation_original"),
        "gene": gene,
        "disease": variant.get("disease"),
        "ground_truth": variant.get("classification"),
        "ground_truth_criteria": variant.get("criteria_met", []),
        "expert_panel": variant.get("expert_panel"),
        "execution_time_ms": elapsed_ms,
        "model_used": MODEL_NAME,
        "aiva_classification": cls_data.get("classification"),
        "aiva_confidence": cls_data.get("confidence"),
        "aiva_criteria": cls_data.get("criteria_met", []),
        "aiva_evidence": cls_data.get("evidence_summary", {}),
        "aiva_rationale": cls_data.get("classification_rationale"),
        "aiva_input_issue": cls_data.get("input_issue", ""),
        "error": cls_data.get("error")
    }


async def evaluate_variant(client: httpx.AsyncClient, variant: dict) -> dict:
    """
    Evaluate variant using multi-phase resolution:
    If standard payload yields 'Not classified', retries with HGVS-only,
    coords-only, or canonical transcript version.
    """
    payloads = generate_payload_variants(variant)
    last_res = None

    for mode, payload in payloads:
        cls_data, elapsed_ms = await call_api(client, payload, variant)
        last_res = build_result_record(variant, cls_data, elapsed_ms)
        clf = cls_data.get("classification")
        if clf and clf not in ("Not classified", "Error", None):
            return last_res
        await asyncio.sleep(1)

    return last_res or build_result_record(variant, {"error": "No valid payload"}, 0)


async def run_astra(input_file: Path, output_file: Path, concurrency: int = 5, max_passes: int = 4):
    """Run full classification pipeline for GPT-6 Astra."""
    if not AIVA_API_TOKEN:
        sys.exit("Error: AIVA_API_TOKEN is not set. Ensure .env is populated.")

    if not input_file.exists():
        sys.exit(f"Error: Input dataset not found: {input_file}")

    output_file.parent.mkdir(parents=True, exist_ok=True)

    variants = []
    with open(input_file, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                variants.append(json.loads(line))

    total = len(variants)
    log("=" * 70)
    log(f"Starting GPT-6 Astra Classification Run")
    log(f"Model:        {MODEL_NAME}")
    log(f"Input:        {input_file} ({total} variants)")
    log(f"Output:       {output_file}")
    log(f"Concurrency:  {concurrency}")
    log("=" * 70)

    headers = {
        "Authorization": f"Bearer {AIVA_API_TOKEN}",
        "Content-Type": "application/json",
        "Accept": "application/json"
    }

    for pass_num in range(1, max_passes + 1):
        classified_ids = load_classified_variant_ids(output_file)
        pending = [v for v in variants if (v.get("id") or v.get("variant_id")) not in classified_ids]

        log(f"--- Pass {pass_num}/{max_passes}: {len(classified_ids)}/{total} completed, {len(pending)} pending ---")
        if not pending:
            log(f"All {total} variants classified successfully!")
            break

        sem = asyncio.Semaphore(concurrency)
        completed_in_pass = 0

        async with httpx.AsyncClient(base_url=AIVA_API_URL, headers=headers, timeout=REQUEST_TIMEOUT) as client:
            async def worker(v: dict):
                nonlocal completed_in_pass
                vid = (v.get("id") or v.get("variant_id") or "")[:8]
                gene = v.get("gene_symbol") or v.get("gene") or "Unknown"

                async with sem:
                    try:
                        record = await evaluate_variant(client, v)
                    except Exception as err:
                        record = build_result_record(v, {"error": str(err)}, 0)

                    with open(output_file, "a", encoding="utf-8") as out_f:
                        out_f.write(json.dumps(record) + "\n")

                    completed_in_pass += 1
                    status = record.get("aiva_classification") or f"ERROR: {record.get('error', '')[:40]}"
                    log(f"[{completed_in_pass}/{len(pending)}] ({len(classified_ids) + completed_in_pass}/{total}) {gene} ({vid}): {status} ({record.get('execution_time_ms', 0)}ms)")
                    await asyncio.sleep(0.5)

            tasks = [worker(v) for v in pending]
            await asyncio.gather(*tasks)

    # Final summary
    final_classified = load_classified_variant_ids(output_file)
    pct = (len(final_classified) / total * 100) if total else 0
    log("=" * 70)
    log(f"GPT-6 Astra Run Finished: {len(final_classified)}/{total} ({pct:.2f}%) classified.")
    log(f"Results: {output_file}")
    log("=" * 70)


def main():
    parser = argparse.ArgumentParser(description="Run GPT-6 Astra variant classification benchmark")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT, help=f"Input JSONL file (default: {DEFAULT_INPUT})")
    parser.add_argument("--output", type=Path, default=OUTPUT_FILE, help=f"Output JSONL file (default: {OUTPUT_FILE})")
    parser.add_argument("--concurrency", type=int, default=5, help="Max concurrency (default: 5)")
    parser.add_argument("--max-passes", type=int, default=4, help="Max retry passes (default: 4)")
    args = parser.parse_args()

    asyncio.run(
        run_astra(
            input_file=args.input,
            output_file=args.output,
            concurrency=args.concurrency,
            max_passes=args.max_passes
        )
    )


if __name__ == "__main__":
    main()
