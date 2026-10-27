"""Helper module for tank level dynamics used by plant.Tank."""


def compute_new_level(current_level, pump_speed_pct, dt, inflow_gain, outflow, area):
    """Compute the next tank level given current state and parameters.

    Applies a first-order balance of inflow vs outflow over dt, scaled by area,
    then clamps the result to be non-negative.
    """
    inflow = inflow_gain * pump_speed_pct
    updated_level = current_level + (inflow - outflow) * dt / area
    return max(0.0, updated_level)
