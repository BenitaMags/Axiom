"""modbus_scaling -- unit conversion constants for ModbusIO fields."""

TEMPERATURE_SCALE = 10.0  # deci-degrees C -> C
PRESSURE_SCALE = 100.0  # centibar -> bar
VALVE_COUNTS_PER_PERCENT = 40.95  # 0-100% -> 0-4095
VALVE_PERCENT_MIN = 0.0
VALVE_PERCENT_MAX = 100.0
