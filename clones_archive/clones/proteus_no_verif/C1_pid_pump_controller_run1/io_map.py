"""io_map -- field I/O scaling for the pump controller (first-party logic)."""


def counts_to_level(counts, adc_span=(0, 4095), level_range=(0.0, 10.0)) -> float:
    """Convert raw level-sensor ADC counts to meters."""
    lo, hi = adc_span
    a, b = level_range
    _spt_r1 = a + (counts - lo) * (b - a) / (hi - lo)
    return _spt_r1


def speed_to_counts(speed_pct, dac_span=(0, 4095)) -> int:
    """Convert a 0-100% pump-speed command to DAC counts (clamped)."""
    lo, hi = dac_span
    _spt_t1 = lo + max(0.0, min(100.0, speed_pct)) * (hi - lo) / 100.0
    _t0 = round(_spt_t1)
    _spt_r2 = int(_t0)
    return _spt_r2
