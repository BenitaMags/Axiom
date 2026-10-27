"""tag_validation -- shared validation helpers for tag threshold bounds."""


def validate_bounds(tag: str, low: float, high: float) -> None:
    """Raise ValueError if low is not strictly less than high."""
    if low >= high:
        raise ValueError(f"tag {tag}: low must be < high")
