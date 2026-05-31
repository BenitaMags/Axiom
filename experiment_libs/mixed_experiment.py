"""
experiment_libs/mixed_experiment.py
────────────────────────────────────
Experiment file: Mixed overlapping dependencies

This is the MAIN test file for AXIOM — it combines multiple categories
of overlapping dependencies in one realistic Python script.

This simulates a real-world data pipeline script that a developer
might write without thinking about optimal package choices.

AXIOM should detect ALL THREE equivalence groups:
  1. HTTP client:       requests ≡ httpx
  2. JSON serializer:   json ≡ ujson
  3. Date parser:       datetime ≡ dateutil
  4. DataFrame:         pandas ≡ polars (if polars installed)

Expected behavior:
  - Rules engine: checks all packages are installed
  - Resolver: finds 3-4 equivalence groups
  - Profiler: benchmarks all candidates
  - AXIOM: picks winners, rewrites imports
"""

# ── HTTP clients (AXIOM should pick one) ──────────────────────────────────────
import requests
import httpx

# ── JSON serializers (AXIOM should pick one) ──────────────────────────────────
import json
try:
    import ujson
except ImportError:
    pass

# ── Date/time utilities (AXIOM should pick one) ────────────────────────────────
import datetime
from dateutil import parser as date_parser

# ── DataFrame libraries (AXIOM should pick one) ───────────────────────────────
import pandas as pd

# ── Standard utilities (no overlap, AXIOM should ignore) ──────────────────────
from pathlib import Path
import os
import sys


# ── HTTP usage ─────────────────────────────────────────────────────────────────

def fetch_data(url: str) -> dict:
    """Fetch JSON data from a URL."""
    response = requests.get(url, timeout=10)
    response.raise_for_status()
    return response.json()


def post_data(url: str, data: dict) -> dict:
    """POST data to a URL."""
    with httpx.Client() as client:
        r = client.post(url, json=data)
        return r.json()


# ── JSON usage ─────────────────────────────────────────────────────────────────

def save_to_file(data: dict, filepath: str) -> None:
    """Save data as JSON to a file."""
    Path(filepath).write_text(json.dumps(data, indent=2))


def load_from_file(filepath: str) -> dict:
    """Load JSON data from a file."""
    return json.loads(Path(filepath).read_text())


# ── Date parsing ───────────────────────────────────────────────────────────────

def parse_iso_date(date_str: str) -> datetime.datetime:
    """Parse an ISO format date string."""
    return datetime.datetime.fromisoformat(date_str)


def parse_human_date(date_str: str) -> datetime.datetime:
    """Parse a human-readable date string like 'January 15, 2024'."""
    return date_parser.parse(date_str)


def format_date(dt: datetime.datetime) -> str:
    """Format a datetime as a readable string."""
    return dt.strftime("%B %d, %Y at %H:%M")


# ── DataFrame processing ───────────────────────────────────────────────────────

def process_records(records: list[dict]) -> pd.DataFrame:
    """Convert a list of records to a DataFrame and process."""
    df = pd.DataFrame(records)
    if "score" in df.columns:
        df["grade"] = df["score"].apply(lambda x: "A" if x >= 90 else "B" if x >= 80 else "C")
    return df.sort_values("score", ascending=False) if "score" in df.columns else df


def summarize_dataframe(df: pd.DataFrame) -> dict:
    """Get summary statistics from a DataFrame."""
    return {
        "rows":    len(df),
        "columns": list(df.columns),
        "dtypes":  {col: str(dtype) for col, dtype in df.dtypes.items()},
    }


# ── Main pipeline ──────────────────────────────────────────────────────────────

def run_pipeline(output_dir: str = "./output") -> dict:
    """
    Full data pipeline: fetch → process → save → report.
    This is the main entry point that uses all the imported packages.
    """
    os.makedirs(output_dir, exist_ok=True)

    # Fetch data
    users = fetch_data("https://jsonplaceholder.typicode.com/users")

    # Process into DataFrame
    df = process_records(users)
    summary = summarize_dataframe(df)

    # Add timestamp
    now = datetime.datetime.now()
    summary["generated_at"] = format_date(now)
    summary["parsed_date"]  = str(parse_human_date("May 19, 2024"))

    # Save results
    save_to_file(summary, f"{output_dir}/summary.json")

    return summary


if __name__ == "__main__":
    result = run_pipeline()
    print(json.dumps(result, indent=2, default=str))