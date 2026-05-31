"""
experiment_libs/dataframe_experiment.py
────────────────────────────────────────
Experiment file: DataFrame libraries

Tests AXIOM's ability to detect that pandas and polars
serve the same role (tabular data manipulation).

Packages involved:
  - pandas  : most popular DataFrame library, ~100ms import, mature ecosystem
  - polars  : Rust-based, 5-10x faster for large datasets, ~50ms import
              Growing rapidly, mostly drop-in for common operations

AXIOM should detect: pandas ≡ polars (same role: DataFrame operations)
Expected winner: polars (faster import, faster execution)
Note: This is a MEDIUM confidence decision — APIs differ in some areas
"""

import pandas as pd
try:
    import polars as pl
except ImportError:
    pl = None


# ── Using pandas ───────────────────────────────────────────────────────────────

def load_csv_pandas(filepath: str) -> pd.DataFrame:
    """Load a CSV file into a pandas DataFrame."""
    return pd.read_csv(filepath)


def filter_rows_pandas(df: pd.DataFrame, column: str, value) -> pd.DataFrame:
    """Filter rows where column equals value."""
    return df[df[column] == value]


def group_and_sum_pandas(df: pd.DataFrame, group_col: str, sum_col: str) -> pd.DataFrame:
    """Group by a column and sum another."""
    return df.groupby(group_col)[sum_col].sum().reset_index()


def get_stats_pandas(df: pd.DataFrame) -> dict:
    """Get basic statistics for all numeric columns."""
    desc = df.describe()
    return desc.to_dict()


def sort_by_column_pandas(df: pd.DataFrame, column: str, ascending: bool = True) -> pd.DataFrame:
    """Sort DataFrame by a column."""
    return df.sort_values(by=column, ascending=ascending)


# ── Using polars ───────────────────────────────────────────────────────────────

def load_csv_polars(filepath: str):
    """Load a CSV file into a polars DataFrame."""
    if pl is None:
        return load_csv_pandas(filepath)
    return pl.read_csv(filepath)


def filter_rows_polars(df, column: str, value):
    """Filter rows where column equals value using polars."""
    if pl is None:
        return filter_rows_pandas(df, column, value)
    return df.filter(pl.col(column) == value)


def group_and_sum_polars(df, group_col: str, sum_col: str):
    """Group by and sum using polars."""
    if pl is None:
        return group_and_sum_pandas(df, group_col, sum_col)
    return df.group_by(group_col).agg(pl.col(sum_col).sum())


def from_dict_pandas(data: dict) -> pd.DataFrame:
    """Create a DataFrame from a dict using pandas."""
    return pd.DataFrame(data)


def from_dict_polars(data: dict):
    """Create a DataFrame from a dict using polars."""
    if pl is None:
        return from_dict_pandas(data)
    return pl.DataFrame(data)


# ── Main ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    sample_data = {
        "name":  ["Alice", "Bob", "Charlie", "Diana"],
        "score": [85, 92, 78, 95],
        "grade": ["B", "A", "C", "A"],
    }

    df_pd = from_dict_pandas(sample_data)
    print("pandas DataFrame:")
    print(df_pd.to_string())

    if pl:
        df_pl = from_dict_polars(sample_data)
        print("\npolars DataFrame:")
        print(df_pl)