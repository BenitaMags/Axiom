"""tag_config_validation -- validation helpers for TagConfig-like objects."""


def ensure_valid_range(tc) -> None:
    """Raise ValueError if tc.low is not strictly less than tc.high."""
    if tc.low >= tc.high:
        raise ValueError(f"tag {tc.tag}: low must be < high")
