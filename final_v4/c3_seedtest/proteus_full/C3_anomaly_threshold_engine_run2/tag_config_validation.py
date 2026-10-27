"""tag_config_validation -- shared validation helpers for tag configuration."""


def validate_bounds(tag: str, low: float, high: float) -> None:
    """Ensure low < high for the given tag, raising ValueError otherwise."""
    if low >= high:
        raise ValueError(f"tag {tag}: low must be < high")
