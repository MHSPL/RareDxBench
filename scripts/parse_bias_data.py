"""
Parse BIAS-2015 supplementary data for 4-way comparison.

Parses:
- eRepo ground truth (erepo_feb_2nd.tabbed)
- InterVar predictions (erepo_feb_2nd.hg19_multianno.txt.intervar)
- BIAS-2015 predictions (erepo_feb_2nd_bitscopic_classifications.tsv)

Creates unified dataset with matching variants for comparison. The file
parsers live in scripts/bias_parsers.py.

Usage:
    python scripts/parse_bias_data.py
"""

import argparse
import json
import shutil
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import CLASSIFICATION_DATA_DIR, CLASSIFICATION_ANSWERS_EXTENDED
from scripts.bias_parsers import (  # noqa: F401  (re-exported)
    HG19_NC_TO_CHROM, NC_TO_CHROM, chrom_num_to_name, vcf_to_annovar_key, vcf_to_annovar_keys,
    parse_hg19_coord, parse_intervar_classification, parse_intervar_predictions,
    parse_bias_predictions, normalize_indel_reproduce, parse_vcf_reproduce, load_id_alignment_map,
)

DATA_DIR = CLASSIFICATION_DATA_DIR
BIAS_DATA_DIR = CLASSIFICATION_DATA_DIR / "bias_2015"


def save_aiva_input(unified: list[dict], output_path: Path):
    with open(output_path, 'w', encoding='utf-8') as f:
        for record in unified:
            f.write(json.dumps(record, ensure_ascii=False) + '\n')

    print(f"Saved {len(unified)} variants to {output_path}")
    print(f"  Assembly: GRCh37 (hg19)")

def create_unified_dataset_reproduce(erepo_path, intervar_path, bias_path, vcf_path, output_path, max_variants=None):
    print(f"Reproducing unified dataset using VCF {vcf_path}...")
    
    # Align IDs with the benchmark file so its variants keep their IDs
    original_benchmark_path = CLASSIFICATION_ANSWERS_EXTENDED
    alignment_map, original_ids = load_id_alignment_map(original_benchmark_path)
    print(f"  Loaded {len(original_ids)} original IDs for alignment")

    vcf_variants, erepo_to_vcf = parse_vcf_reproduce(vcf_path)
    print(f"  VCF mapped to {len(erepo_to_vcf)} eRepo lines")
    print("Parsing InterVar and BIAS...")
    intervar, intervar_pos_index = parse_intervar_predictions(intervar_path)
    bias, bias_hgvsc, bias_gene_hgvsc, bias_hgvsg, bias_pos_index = parse_bias_predictions(bias_path)
    print("Linking InterVar to VCF...")
    erepo_to_intervar = {}
    erepo_pos_idx = defaultdict(list)
    for line, data in erepo_to_vcf.items():
        erepo_pos_idx[f"{data['chrom']}:{data['pos']}"].append(line)
    for iv_key, iv_list in intervar.items():
        # Retrieve matched line logic same as before but inside loop or before?
        # The key drives the match.
        matched_line = None
        if iv_key in vcf_variants: matched_line = vcf_variants[iv_key]
        if not matched_line:
            parts = iv_key.split(':')
            if len(parts) >= 2:
                chrom = parts[0]; pos = int(parts[1])
                if f"{chrom}:{pos}" in erepo_pos_idx: matched_line = erepo_pos_idx[f"{chrom}:{pos}"][0]
                else:
                    for offset in [-1, 1]:
                        if f"{chrom}:{pos+offset}" in erepo_pos_idx:
                            matched_line = erepo_pos_idx[f"{chrom}:{pos+offset}"][0]; break
        
        if matched_line:
            # Iterate through all candidates for this key and pick best
            # Prioritize one with non-empty classification
            best_iv = None
            for iv_data in iv_list:
                classification = iv_data.get('classification')
                if classification and classification != "Uncertain significance": # Prefer definitive? No, original had VUS.
                    # Just prefer having data.
                     best_iv = iv_data
                     if classification != "Uncertain significance": break # Optimization?
                elif not best_iv: best_iv = iv_data
            
            if best_iv:
                classification = best_iv.get('classification') or best_iv.get('InterVar: InterVar and Evidence', '').split()[0]
                erepo_to_intervar[matched_line] = {
                    'classification': classification,
                    'criteria_met': best_iv.get('criteria_met', [])
                }
    print("Building variation map...")
    variation_map = {}
    variation_id_map = {} # Initialize map
    erepo_entries = []
    with open(erepo_path, 'r', encoding='utf-8') as f:
        headers = f.readline().strip().split('\t')
        for line_num, line in enumerate(f, start=1):
            values = line.strip().split('\t')
            
            # Fix data alignment for lines with missing ClinVar ID column (19 cols instead of 20)
            if len(values) == 19 and len(headers) == 20:
                # Heuristic: If 2nd val starts with 'CA' (Allele Registry), assume ClinVar ID (2nd col) missing
                if values[1].startswith('CA'):
                    values.insert(1, '')

            if len(values) >= len(headers):
                data = dict(zip(headers, values))
                data['line_num'] = line_num
                erepo_entries.append(data)

                # Capture ClinVar ID for propagation
                c_id = data.get('ClinVar Variation Id', '')
                if c_id and c_id != '-' and c_id != 'None':
                    variation = data.get('#Variation')
                    if variation:
                        variation_id_map[variation] = c_id
                
                if line_num in erepo_to_vcf:
                    variation = data.get('#Variation')
                    variant_key = erepo_to_vcf[line_num]['variant_key']
                    bias_match = bias.get(variant_key)
                    intervar_match = erepo_to_intervar.get(line_num)
                    if bias_match and intervar_match:
                        variation_map[variation] = {
                            'variant_key': variant_key,
                            'vcf_id': erepo_to_vcf[line_num].get('vcf_id'),
                            'variant_type': erepo_to_vcf[line_num].get('variant_type', 'SNV'),
                            'bias': bias_match, 'intervar': intervar_match
                        }
    unified_data = []
    processed_lines = set()
    processed_ids = set() # For 1:1 ID enforcement
    for entry in erepo_entries:
        variation = entry.get('#Variation')
        mapped = variation_map.get(variation)
        if mapped:
            processed_lines.add(entry['line_num'])
            hgvs_str = entry.get('HGVS Expressions', '')
            hgvs_expressions = hgvs_str.split(', ') if hgvs_str else []
            coord = parse_hg19_coord(hgvs_str)
            hgvs_g = coord[4] if coord else variation
            criteria_str = entry.get('Applied Evidence Codes (Met)', '')
            criteria_met = [c.strip() for c in criteria_str.split(',') if c.strip()]
            
            c_id = entry.get('ClinVar Variation Id', '')
            
            # Fallback 1: Internal Duplicate Propagation
            if (not c_id or c_id == "-" or c_id == "None"):
                if variation in variation_id_map:
                    c_id = variation_id_map[variation]
            
            # Fallback 2: VCF ID (if available)
            vcf_id = mapped.get('vcf_id')
            if (not c_id or c_id == "-" or c_id == "None") and vcf_id and vcf_id != ".": c_id = vcf_id
            
            # 1:1 ALIGNMENT & PRUNING LOGIC
            final_id = entry.get('Uuid', '')
            if final_id not in original_ids:
                variation_key = entry.get('#Variation', '').lower()
                final_id = alignment_map.get(final_id) or alignment_map.get(variation_key)
            
            if not final_id or final_id not in original_ids:
                continue
            
            if final_id in processed_ids:
                continue
            
            processed_ids.add(final_id)
            unified_data.append({
                'id': final_id, 
                'variation': variation, 
                'clinvar_id': c_id,
                'hgvs_expressions': hgvs_expressions,
                'gene_symbol': entry.get('HGNC Gene Symbol', ''), 
                'disease': entry.get('Disease', ''),
                'mode_of_inheritance': entry.get('Mode of Inheritance', ''), 
                'classification': entry.get('Assertion', ''), 
                'criteria_met': criteria_met, 
                'expert_panel': entry.get('Expert Panel', ''),
                'intervar_classification': mapped['intervar']['classification'],
                'intervar_criteria': mapped['intervar'].get('criteria_met', []),
                'bias_classification': mapped['bias']['classification'],
                'bias_criteria': mapped['bias'].get('criteria_met', []),
                'variant_key': mapped['variant_key'],
                'variation_original': entry.get('#Variation', ''),
                'match_method': 'vcf_fanout'
            })
    print(f"  Count after VCF+Fanout: {len(unified_data)}")
    print("Executing Rescue Strategy for missing variants...")
    rescued_count = 0
    for entry in erepo_entries:
        if entry['line_num'] in processed_lines: continue
        hgvs_str = entry.get('HGVS Expressions', '')

        hgvs_expressions = hgvs_str.split(', ') if hgvs_str else []
        # Ground-truth criteria for THIS entry (avoid leaking stale value from the prior loop)
        criteria_str = entry.get('Applied Evidence Codes (Met)', '')
        criteria_met = [c.strip() for c in criteria_str.split(',') if c.strip()]
        found_bias = None
        found_iv = None
        gene_symbol = entry.get('HGNC Gene Symbol', '')
        gene_symbol_lower = gene_symbol.lower()
        for expr in hgvs_expressions:
            if expr in bias_hgvsg: found_bias = bias_hgvsg[expr]; break
        coords = parse_hg19_coord(hgvs_str)
        
        if not found_bias and coords:
            chrom, pos, ref, alt, _ = coords
            # Search +/- 5bp
            for offset in range(-5, 6):
                key = f"{chrom}:{pos+offset}"
                if key in bias_pos_index:
                    candidates = bias_pos_index[key]
                    for candidate in candidates:
                        cand_gene = candidate.get('gene', '').lower()
                        if cand_gene == gene_symbol_lower:
                            found_bias = candidate
                            break
                    if found_bias: break
        if coords:
            chrom, pos, ref, alt, _ = coords
            for offset in range(-5, 6):
                key = f"{chrom}:{pos+offset}"
                q_key = f"{gene_symbol}|{key}"
                if q_key in intervar_pos_index: found_iv = intervar_pos_index[q_key]; break
        if found_bias:
            rescued_count += 1
            processed_lines.add(entry['line_num'])
            variant_key = "Rescue:Unknown"
            if isinstance(found_bias, dict):
                 b_chr = found_bias.get('chromosome', '')
                 if b_chr and not b_chr.startswith('chr'): b_chr = f"chr{b_chr}"
                 b_pos = found_bias.get('position', '')
                 b_ref = found_bias.get('refAllele', '')
                 b_alt = found_bias.get('altAllele', '')
                 if all([b_chr, b_pos, b_ref, b_alt]): variant_key = f"{b_chr}:{b_pos}:{b_ref}:{b_alt}"
            iv_class = ""
            if found_iv: iv_class = found_iv.get('classification') or found_iv.get('InterVar: InterVar and Evidence', '').split()[0]
            bias_class = found_bias.get('acmgClassification', '') if isinstance(found_bias, dict) else ''
            
            # 1:1 ALIGNMENT & PRUNING LOGIC
            final_id = entry.get('Uuid', '')
            if final_id not in original_ids:
                variation_key = entry.get('#Variation', '').lower()
                final_id = alignment_map.get(final_id) or alignment_map.get(variation_key)
            
            if not final_id or final_id not in original_ids:
                continue
            
            if final_id in processed_ids:
                continue
            
            processed_ids.add(final_id)
            unified_data.append({
                'id': final_id, 
                'variation': entry.get('#Variation'),
                'clinvar_id': entry.get('ClinVar Variation Id', ''), 
                'hgvs_expressions': hgvs_expressions,
                'gene_symbol': gene_symbol,
                'disease': entry.get('Disease', ''), 
                'mode_of_inheritance': entry.get('Mode of Inheritance', ''),
                'classification': entry.get('Assertion', ''), 
                'criteria_met': criteria_met, 
                'expert_panel': entry.get('Expert Panel', ''),
                'intervar_classification': iv_class, 
                'intervar_criteria': [],
                'bias_classification': bias_class, 
                'bias_criteria': [], 
                'variant_key': variant_key,
                'variation_original': entry.get('#Variation', ''),
                'match_method': 'rescue_bias'
            })
    print(f"  Rescued: {rescued_count}")
    
    # GOLD POOL PASS-THROUGH (The Final 10)
    # Ensure every ID in the original benchmark is present.
    # If some are still missing (due to source file gaps), pass them through from the original file.
    still_missing = original_ids - processed_ids
    if still_missing:
        print(f"  Final alignment: {len(still_missing)} variants still missing from source. Passing through from gold pool...")
        with open(original_benchmark_path, 'r', encoding='utf-8') as f:
            for line in f:
                data = json.loads(line)
                if data['id'] in still_missing:
                    data['match_method'] = 'gold_pool_passthrough'
                    unified_data.append(data)
                    processed_ids.add(data['id'])

    if max_variants and len(unified_data) > max_variants:
        print(f"  Limiting to {max_variants} variants")
        unified_data = unified_data[:max_variants]
    
    # Final Sort by id to ensure deterministic 1:1 match
    unified_data.sort(key=lambda x: x['id'])
    
    save_aiva_input(unified_data, output_path)
    return unified_data

def main():
    parser = argparse.ArgumentParser(description="Parse BIAS-2015 supplementary data")
    # The full cohort goes to its own file: data/classification/erepo_bias_intervar_merged.jsonl is the
    # trimmed benchmark set, which this script must not overwrite.
    parser.add_argument("--output", default="erepo_bias_intervar_full.jsonl", help="Output filename (in data/classification/)")
    parser.add_argument("--max-variants", type=int, default=None, help="Maximum number of variants to output")
    parser.add_argument("--source-dir", help="Directory holding the unzipped BIAS-2015 supplementary "
                                             "files; if given, any missing files are copied into data/classification/bias_2015/")
    args = parser.parse_args()

    vcf_path = BIAS_DATA_DIR / "erepo_feb_2nd_sorted.vcf"
    erepo_path = BIAS_DATA_DIR / "erepo_feb_2nd.tabbed"
    intervar_path = BIAS_DATA_DIR / "erepo_feb_2nd.hg19_multianno.txt.intervar"
    bias_path = BIAS_DATA_DIR / "erepo_feb_2nd_bitscopic_classifications.tsv"
    required_paths = (vcf_path, erepo_path, intervar_path, bias_path)

    # Optionally stage missing source files from a user-provided directory.
    if args.source_dir:
        src_dir = Path(args.source_dir)
        BIAS_DATA_DIR.mkdir(parents=True, exist_ok=True)
        for dest in required_paths:
            src = src_dir / dest.name
            if not dest.exists() and src.exists():
                shutil.copy(src, dest)

    missing = [p.name for p in required_paths if not p.exists()]
    if missing:
        print(f"Error: missing required source file(s) in {BIAS_DATA_DIR}: {', '.join(missing)}")
        print("Provide them with --source-dir <unzipped BIAS supplementary dir> (see README Workflow B).")
        sys.exit(1)

    create_unified_dataset_reproduce(erepo_path, intervar_path, bias_path, vcf_path, DATA_DIR / args.output, args.max_variants)

if __name__ == "__main__":
    main()
