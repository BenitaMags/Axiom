"""io_map -- field I/O scaling for the pump controller (first-party logic)."""

from range_transform import LinearRange


def counts_to_level(counts, adc_span=(0, 4095), level_range=(0.0, 10.0)) -> float:
    """Convert raw level-sensor ADC counts to meters."""
    transform = LinearRange(source=adc_span, target=level_range)
    return transform.scale(counts)


def speed_to_counts(speed_pct, dac_span=(0, 4095)) -> int:
    """Convert a 0-100% pump-speed command to DAC counts (clamped)."""
    transform = LinearRange(source=(0.0, 100.0), target=dac_span)
    return transform.scale_clamped_rounded(speed_pct)
