"""modbus_result -- normalize heterogeneous Modbus read/write results.

ModbusIO's client attribute can be a pyModbusTCP-backed adapter (already
normalized), a pymodbus-style legacy response object exposing
isError()/registers, or a raw pyModbusTCP client returning its native
list/None or bool/None conventions. These helpers collapse all of those
shapes down to the single plain value (or IOError) that ModbusIO's
arithmetic and callers expect.
"""


def _axiom_normalize_read_result_wrapper(result):
    """
    self.client.read_holding_registers(...) can be reached through several
    shapes depending on what ModbusIO was constructed with: a pyModbusTCP-
    backed _AxiomModbusClientAdapter (which already normalizes to a single
    int via _axiom_read_holding_registers_wrapper), a test double shaped like
    pymodbus's legacy response object (exposing .isError() and .registers),
    or a raw pyModbusTCP client returning its native list-on-success/
    None-on-failure convention directly. pymodbus itself always handed
    callers a response object checked via isError()/registers, so ModbusIO's
    arithmetic (dividing the result by 10.0/100.0) assumed a bare number came
    out the other end. This wrapper inspects the shape it actually received
    and normalizes every case to that same single int: legacy response
    objects are checked via isError() (raising IOError on failure, exactly as
    pymodbus's callers would) and unwrapped via registers[0]; a None reply is
    treated as the pyModbusTCP failure signal and raises IOError to match;
    a list/tuple (pyModbusTCP's raw success shape) is unwrapped to its first
    element; anything already numeric (an adapter-normalized value) passes
    through unchanged.
    """
    if hasattr(result, "isError"):
        if result.isError():
            raise IOError("Modbus read failed")
        return result.registers[0]
    if result is None:
        raise IOError("Modbus read failed")
    if isinstance(result, (list, tuple)):
        return result[0]
    return result


def _axiom_normalize_write_result_wrapper(result, counts):
    """
    self.client.write_register(...) can likewise be reached through several
    shapes: a pyModbusTCP-backed _AxiomModbusClientAdapter (already
    normalized to `counts` via _axiom_write_register_wrapper), a test double
    shaped like pymodbus's legacy response object (exposing .isError()), or a
    raw pyModbusTCP client returning True/False/None instead. pymodbus's
    write_register callers checked wr.isError() and otherwise treated the
    call as having written `counts`. This wrapper reproduces that contract
    regardless of which shape it receives: a legacy response object is
    checked via isError() (raising IOError on failure); a falsy result
    (None/False, pyModbusTCP's raw failure signal) raises IOError; any other
    (truthy or adapter-normalized) result is treated as success and reported
    as `counts`, matching what the original write_register callers returned.
    """
    if hasattr(result, "isError"):
        if result.isError():
            raise IOError("Modbus write failed")
        return counts
    if not result:
        raise IOError("Modbus write failed")
    return counts
