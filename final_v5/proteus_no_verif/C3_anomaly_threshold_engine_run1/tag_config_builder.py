"""tag_config_builder -- helper to construct and validate tag configuration maps."""


def _validate(tc):
    if tc.low >= tc.high:
        raise ValueError(f"tag {tc.tag}: low must be < high")
    return tc


def build_tag_configs(config: dict, factory) -> dict:
    """Construct a {tag: factory(...)} mapping from config["tags"], validating bounds."""
    result = {}
    for entry in config["tags"]:
        tc = factory(tag=entry["tag"], high=entry["high"], low=entry["low"])
        _validate(tc)
        result[tc.tag] = tc
    return result
