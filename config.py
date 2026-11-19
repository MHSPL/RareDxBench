"""
Configuration for RareDx Bench.
"""

import os
from pathlib import Path
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass

# Paths
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
INPUTS_DIR = BASE_DIR / "inputs"

# One tree per benchmark: data/<benchmark>/ holds its inputs and answers,
# results/<benchmark>/ one folder per model run.
CLASSIFICATION_DATA_DIR = DATA_DIR / "classification"
CLASSIFICATION_RESULTS_DIR = BASE_DIR / "results" / "classification"
RANKING_DATA_DIR = DATA_DIR / "ranking"
RANKING_RESULTS_DIR = BASE_DIR / "results" / "ranking"

# Classification benchmark: the variants without classifications (to run a model
# on), and the same variants with the expert, BIAS-2015 and InterVar answers.
CLASSIFICATION_INPUT = CLASSIFICATION_DATA_DIR / "aiva_bench_input.jsonl"
CLASSIFICATION_ANSWERS = CLASSIFICATION_DATA_DIR / "erepo_bias_intervar_merged.jsonl"

# Ad-hoc runs and analyses (git-ignored)
RESULTS_DIR = BASE_DIR / "scratch"

# AIVA API Configuration
AIVA_API_URL = os.getenv("AIVA_API_URL", "https://api.aivaportal.com")
AIVA_API_TOKEN = os.getenv("AIVA_API_TOKEN") or os.getenv("AIVA_API_KEY", "")

# Classification settings
DEFAULT_ASSEMBLY = os.getenv("DEFAULT_ASSEMBLY", "GRCh37")  # eRepo baseline is GRCh37 (hg19)
CLASSIFICATION_TYPE = "acmg"  # acmg for germline, amp for somatic
DEFAULT_MODEL = os.getenv("DEFAULT_MODEL", "google/gemini-3-pro-preview")  # None uses AIVA default (gemini-2.5-pro), or set e.g. "openai/gpt-4o"

# Request settings
CONCURRENCY_LIMIT = int(os.getenv("CONCURRENCY_LIMIT", "5"))  # Keep low to avoid rate limits
REQUEST_TIMEOUT = 300  # 5 minutes
MAX_RETRIES = 5
RETRY_DELAY = 10  # seconds base delay
RATE_LIMIT_DELAY = 30  # seconds to wait on 429

# Sampling settings
DEFAULT_SAMPLE_SIZE = int(os.getenv("DEFAULT_SAMPLE_SIZE", "200"))
RANDOM_SEED = 42

# Classification categories
CLASSIFICATION_LABELS = [
    "Pathogenic",
    "Likely Pathogenic",
    "Uncertain Significance",
    "Likely Benign",
    "Benign"
]

# Mapping for directional (3-class) comparison
CLASSIFICATION_TO_DIRECTION = {
    "Pathogenic": "pathogenic",
    "Likely Pathogenic": "pathogenic",
    "Uncertain Significance": "vus",
    "Likely Benign": "benign",
    "Benign": "benign",
    # AIVA variations
    "VUS": "vus",
    "VUS (leaning pathogenic)": "vus",
    "VUS (leaning benign)": "vus",
}

DIRECTION_LABELS = ["pathogenic", "vus", "benign"]

# wInterVar API Configuration
WINTERVAR_API_URL = os.getenv("WINTERVAR_API_URL", "http://wintervar.wglab.org/api_new.php")
WINTERVAR_ASSEMBLY = os.getenv("WINTERVAR_ASSEMBLY", "hg38")  # hg19 or hg38
