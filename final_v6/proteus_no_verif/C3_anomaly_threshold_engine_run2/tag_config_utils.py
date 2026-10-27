"""Helper utilities for validating tag configuration entries."""


def validate_bounds(tc) -> None:
    """Ensure tc.low < tc.high, raising ValueError with a standard message otherwise."""
    if tc.low >= tc.high:
        raise ValueError(f"tag {tc.tag}: low must be < high")
