"""tag_validation -- shared validation helpers for tag threshold configs."""


def validate_bounds(tag: str, low: float, high: float) -> None:
    if low >= high:
        raise ValueError(f"tag {tag}: low must be < high")
