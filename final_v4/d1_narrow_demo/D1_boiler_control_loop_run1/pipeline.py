"""Pipeline stages used to run one boiler supervisory cycle.

Each stage is a small, named function so the supervisor can compose them
instead of inlining every Modbus/control call in one long method.
"""
from control import BoilerControl
from transport import ModbusIO


def read_sensors(io: ModbusIO):
    """Read temperature then pressure, in that order, and return both."""
    temperature = io.read_temperature()
    pressure = io.read_pressure()
    return temperature, pressure


def compute_command(control: BoilerControl, temperature: float, pressure: float, dt: float):
    """Advance the control law one step and return the valve command."""
    return control.step(temperature, pressure, dt=dt)


def actuate(io: ModbusIO, command):
    """Write the valve command to the transport and return the raw counts."""
    return io.write_valve(command)
