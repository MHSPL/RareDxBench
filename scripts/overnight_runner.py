#!/usr/bin/env python3
"""
Shared overnight runner: classifies the frozen 2,894-variant benchmark
(data/classification/aiva_bench_input.jsonl) with one model through the AIVA
API, appending one JSON line per attempt to the model's results file.

Model-specific entry points (scripts/run_argon_overnight.py,
scripts/run_astra_overnight.py) only set the model name and output file.

Rules:
1. Stop the whole run only on billing exhaustion (HTTP 402 or a structured
   quota/billing error).
2. HTTP 500 (e.g. max agent turns exceeded) and 4xx errors are recorded for
   that variant; the run continues.
3. Rate limits (429), gateway errors (502/503/504), timeouts and network
   errors are retried up to MAX_RETRIES times with exponential backoff.
4. Variants still unclassified are swept again, up to max_passes passes.
"""

import argparse
import asyncio
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Set, Tuple

import httpx

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from config import (
    AIVA_API_URL, AIVA_API_TOKEN, DEFAULT_ASSEMBLY, CLASSIFICATION_TYPE,
    CLASSIFICATION_INPUT, CLASSIFICATION_ANSWERS,
)
from scripts.classify import get_variant_data

MAX_RETRIES = 4
REQUEST_TIMEOUT = 300  # seconds per call

BILLING_MARKERS = (
    "insufficient_quota", "quota_exceeded", "out_of_credits",
    "insufficient credit", "billing account", "payment required",
    "exceeded your current quota",
)

GroundTruth = Dict[str, Tuple[Optional[str], list]]


class FatalRunError(Exception):
    """Raised on billing exhaustion, which stops the whole run."""


def log(msg: str):
    """Timestamped logging to stdout (captured by screen / nohup)."""
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def short_id(vid) -> str:
    return (vid or "")[:8]


def is_billing_error(status_code: int, text: str) -> bool:
    """True for an actual billing or credit problem."""
    if status_code == 200:
        return False
    if status_code == 402:
        return True
    try:
        err_json = json.loads(text)
    except Exception:
        return False
    if not isinstance(err_json, dict):
        return False
    err_msg = str(err_json.get("detail") or err_json.get("error") or err_json.get("message") or "").lower()
    return any(m in err_msg for m in BILLING_MARKERS)


def load_classified_variant_ids(output_path: Path) -> Set[str]:
    """Variant ids with a successful classification in the results file."""
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
            if vid and not rec.get("error") and cls and cls != "Not classified":
                classified.add(vid)
    return classified


def load_ground_truth_map(path: Path = CLASSIFICATION_ANSWERS) -> GroundTruth:
    """Variant id -> (expert classification, expert criteria) from the answers file."""
    gt_map = {}
    if not path.exists():
        return gt_map
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            vid = rec.get("id") or rec.get("variant_id")
            if vid:
                gt_map[vid] = (rec.get("classification"), rec.get("criteria_met") or [])
    return gt_map


def clean_hgvs(raw_var: str) -> str:
    """Drop the protein change and the gene symbol: NM_004958.4(MTOR):c.1A>G (p.X) -> NM_004958.4:c.1A>G."""
    cleaned = re.sub(r"\s*\(p\.[^\)]+\)", "", raw_var or "").strip()
    return re.sub(r"\(.*?\):", ":", cleaned)


def extract_variant_payload(variant: dict) -> dict:
    """Best representation for the AIVA API: clean HGVS plus coordinates from variant_key."""
    cleaned = clean_hgvs(variant.get("variation") or variant.get("variation_original") or "")

    coords = None
    parts = (variant.get("variant_key") or "").split(":")
    if len(parts) == 4:
        coords = {"chromosome": parts[0].replace("chr", ""), "position": int(parts[1]), "ref": parts[2], "alt": parts[3]}

    if cleaned and any(t in cleaned for t in (":c.", ":g.", ":n.")) and "(" not in cleaned:
        return {"hgvs": cleaned, **(coords or {})}
    if coords:
        return coords
    return get_variant_data(variant)


def base_record(variant: dict, gt_map: GroundTruth, model: str) -> dict:
    """Fields every results line carries, with the expert answer filled from the answers file."""
    vid = variant.get("id") or variant.get("variant_id")
    gt_cls = variant.get("classification")
    gt_crit = variant.get("criteria_met") or []
    if (not gt_cls or not gt_crit) and vid in gt_map:
        gt_cls = gt_cls or gt_map[vid][0]
        gt_crit = gt_crit or gt_map[vid][1]
    return {
        "variant_id": vid,
        "variant": variant.get("variation"),
        "gene": variant.get("gene_symbol") or variant.get("gene") or "Unknown",
        "disease": variant.get("disease") or "",
        "ground_truth": gt_cls,
        "ground_truth_criteria": gt_crit,
        "expert_panel": variant.get("expert_panel") or "",
        "model_used": model,
    }


def error_detail_500(resp: httpx.Response) -> str:
    try:
        detail = resp.json().get("detail")
        err = detail.get("error", {}) if isinstance(detail, dict) else {}
        return err.get("details") or err.get("message") or str(detail) or resp.text[:200]
    except Exception:
        return resp.text[:200]


async def classify_variant(client: httpx.AsyncClient, variant: dict, gt_map: GroundTruth, model: str) -> dict:
    """Classify one variant, retrying transient failures; raises FatalRunError on billing exhaustion."""
    rec = base_record(variant, gt_map, model)
    tag = f"{rec['gene']} ({short_id(rec['variant_id'])})"

    variant_data = extract_variant_payload(variant)
    if not variant_data:
        return {**rec, "error": "No valid variant identifier or coordinates", "execution_time_ms": 0}

    payload = {
        "variant_data": variant_data,
        "classification_type": CLASSIFICATION_TYPE,
        "assembly": variant.get("genome_build") or DEFAULT_ASSEMBLY,
        "model": model,
        "phenotype_terms": rec["disease"],
        "description": f"Gene: {rec['gene']}",
        "additional_context": f"Mode of inheritance: {variant.get('mode_of_inheritance', 'Unknown')}",
    }

    last_error, elapsed_ms = None, 0

    async def retry(kind: str, base: int, attempt: int) -> bool:
        if attempt >= MAX_RETRIES:
            return False
        backoff = base * (2 ** attempt)
        log(f"  [{kind} for {tag}] Retrying in {backoff}s (attempt {attempt + 1}/{MAX_RETRIES})...")
        await asyncio.sleep(backoff)
        return True

    for attempt in range(MAX_RETRIES + 1):
        start = time.time()
        try:
            resp = await client.post("/api/analyze/classify-variant", json=payload, timeout=httpx.Timeout(REQUEST_TIMEOUT))
            elapsed_ms = int((time.time() - start) * 1000)

            if resp.status_code != 200 and is_billing_error(resp.status_code, resp.text):
                msg = f"FATAL BILLING ERROR: HTTP {resp.status_code}: {resp.text[:300]}"
                log(msg)
                raise FatalRunError(msg)
            if resp.status_code == 500:
                last_error = f"HTTP 500: {error_detail_500(resp)}"
                log(f"  [HTTP 500 for {tag}]: {last_error[10:]} (recording error, continuing with next variant)")
                break
            if resp.status_code == 429:
                if await retry("429 Rate Limit", 15, attempt):
                    continue
                last_error = f"HTTP 429 Rate limit exceeded after {MAX_RETRIES} retries"
                break
            if 400 <= resp.status_code < 500:
                last_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
                break
            if resp.status_code in (502, 503, 504):
                if await retry(f"HTTP {resp.status_code} Gateway error", 5, attempt):
                    continue
                last_error = f"HTTP {resp.status_code} after {MAX_RETRIES} retries"
                break

            resp.raise_for_status()
            data = resp.json()
            cls_data = data.get("data", data)
            return {
                **rec,
                "execution_time_ms": elapsed_ms,
                "aiva_classification": cls_data.get("classification"),
                "aiva_confidence": cls_data.get("confidence"),
                "aiva_criteria": cls_data.get("criteria_met", []),
                "aiva_evidence": cls_data.get("evidence_summary", {}),
                "aiva_acmg_score": cls_data.get("acmg_score"),
                "aiva_rationale": cls_data.get("classification_rationale"),
                "aiva_input_issue": cls_data.get("input_issue", ""),
            }
        except FatalRunError:
            raise
        except httpx.TimeoutException:
            elapsed_ms = int((time.time() - start) * 1000)
            last_error = f"Timeout after {REQUEST_TIMEOUT}s"
            if not await retry("Timeout", 10, attempt):
                break
        except Exception as e:
            elapsed_ms = int((time.time() - start) * 1000)
            last_error = str(e)
            if not await retry(f"Network error: {e}", 5, attempt):
                break

    return {**rec, "execution_time_ms": elapsed_ms, "error": last_error}


def load_variants(input_file: Path) -> list:
    with open(input_file, "r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


async def run_overnight(model: str, output_file: Path, input_file: Path, concurrency: int = 7, max_passes: int = 4):
    """Classify every variant in input_file, sweeping unclassified ones again up to max_passes times."""
    output_file.parent.mkdir(parents=True, exist_ok=True)
    gt_map = load_ground_truth_map()
    variants = load_variants(input_file)
    total = len(variants)
    headers = {"Authorization": f"Bearer {AIVA_API_TOKEN}", "Content-Type": "application/json"}

    log("=" * 70)
    log(f"STARTING OVERNIGHT RUN: {model}")
    log(f"Input Dataset: {input_file} ({total} variants)")
    log(f"Output File:   {output_file}")
    log(f"Concurrency:   {concurrency}")
    log("Stopping rules: stop only on billing exhaustion (HTTP 402 or quota error)")
    log("Error handling: HTTP 500 and timeouts are recorded per variant; the run continues")
    log(f"Retry rules:    up to {MAX_RETRIES} retries on rate limits (429) and transient errors")
    log("Pass rules:     multi-pass sweep over unclassified or errored variants")
    log("=" * 70)

    for pass_num in range(1, max_passes + 1):
        classified_ids = load_classified_variant_ids(output_file)
        pending = [v for v in variants if (v.get("id") or v.get("variant_id")) not in classified_ids]
        log(f"--- PASS {pass_num}/{max_passes}: {len(classified_ids)}/{total} classified, {len(pending)} pending ---")
        if not pending:
            log(f"SUCCESS: All {total} variants have been successfully classified!")
            break

        semaphore = asyncio.Semaphore(concurrency)
        done = 0

        async with httpx.AsyncClient(base_url=AIVA_API_URL.rstrip("/"), headers=headers) as client:
            async def process_one(v: dict):
                nonlocal done
                async with semaphore:
                    try:
                        res = await classify_variant(client, v, gt_map, model)
                    except FatalRunError as fatal:
                        log(f"ABORTING RUN DUE TO FATAL ERROR: {fatal}")
                        raise
                    except Exception as exc:
                        res = {**base_record(v, gt_map, model), "execution_time_ms": 0, "error": f"Worker error: {exc}"}
                        log(f"Unexpected error in worker for {res['gene']} ({short_id(res['variant_id'])}): {exc}")

                    with open(output_file, "a", encoding="utf-8") as out_f:
                        out_f.write(json.dumps(res) + "\n")

                    done += 1
                    status = res.get("aiva_classification") or f"ERROR: {(res.get('error') or '')[:40]}"
                    log(f"[{done}/{len(pending)} in pass] ({len(classified_ids) + done}/{total}) "
                        f"{res['gene']} ({short_id(res['variant_id'])}): {status} ({res.get('execution_time_ms', 0)}ms)")
                    await asyncio.sleep(0.5)  # brief spacing between dispatches

            try:
                await asyncio.gather(*(process_one(v) for v in pending))
            except FatalRunError:
                log("Fatal stopping condition triggered. Terminating runner immediately.")
                return

    final = load_classified_variant_ids(output_file)
    pct = len(final) / total * 100 if total else 0
    log("=" * 70)
    log(f"OVERNIGHT RUN SUMMARY: {len(final)} / {total} variants classified ({pct:.2f}%)")
    log(f"Results file: {output_file}")
    log("=" * 70)


def main(model: str, output_file: Path, description: str, default_concurrency: int = 7):
    """Command-line entry point shared by the per-model runner scripts."""
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--input", type=Path, default=CLASSIFICATION_INPUT,
                        help=f"Path to input JSONL file (default: {CLASSIFICATION_INPUT})")
    parser.add_argument("--concurrency", type=int, default=default_concurrency,
                        help=f"Concurrency limit (default: {default_concurrency})")
    parser.add_argument("--max-passes", type=int, default=4,
                        help="Max retry passes for unclassified variants (default: 4)")
    args = parser.parse_args()
    asyncio.run(run_overnight(model, output_file, args.input, args.concurrency, args.max_passes))
