"""The Connector must not write an adapter that catches everything.

Two consecutive D1 runs produced opposite adapters from the same prompt: one
wrote ``except mt_exc.ModbusError`` -- modbus-tk's protocol failure, translated
into the ``.isError()`` status the call sites already check -- and the next
wrote ``except Exception`` twice. A blind judge rated the second MAJOR:
"every exception including socket timeouts and connection loss is swallowed",
because pymodbus propagated those and reported only protocol failures through
the response object.

The prompt has forbidden ``except Exception:`` in two places since the first
pilot. It is not enough on its own, so the offending lines are detected and
handed back to the edit session.

Re-raising is not a defect however broadly it catches: the error still reaches
the caller, which is the contract. And this must never REVERT the edit --
swallowing is a fidelity defect in a working adapter, and discarding it turns a
migration into a no-op, which is the overcorrection an earlier round of this
check actually produced on D1.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from smart_loader.agents.connector_agent import _blanket_except_sites  # noqa: E402

WRAPPER = ["_axiom_wrapper"]


def test_except_exception_that_returns_is_reported():
    src = ("def _axiom_wrapper(c):\n"
           "    def read(a):\n"
           "        try:\n"
           "            return c.execute(a)\n"
           "        except Exception:\n"
           "            return _R(error=True)\n"
           "    return read\n")
    hits = _blanket_except_sites(src, WRAPPER)
    assert len(hits) == 1 and "read()" in hits[0]


def test_bare_except_is_reported():
    src = ("def _axiom_wrapper(c):\n"
           "    try:\n"
           "        return c.execute()\n"
           "    except:\n"
           "        return None\n")
    hits = _blanket_except_sites(src, WRAPPER)
    assert len(hits) == 1 and "bare except" in hits[0]


def test_a_narrow_handler_is_clean():
    """Translating the target's own error into the original's status is correct."""
    src = ("def _axiom_wrapper(c):\n"
           "    def read(a):\n"
           "        try:\n"
           "            return c.execute(a)\n"
           "        except mt_exc.ModbusError:\n"
           "            return _R(error=True)\n"
           "    return read\n")
    assert _blanket_except_sites(src, WRAPPER) == []


def test_broad_but_re_raising_is_clean():
    src = ("def _axiom_wrapper(c):\n"
           "    try:\n"
           "        return c.execute()\n"
           "    except Exception as exc:\n"
           "        raise IOError('failed') from exc\n")
    assert _blanket_except_sites(src, WRAPPER) == []


def test_one_handler_is_reported_once():
    """Walking a wrapper reaches a nested helper's handler under both names."""
    src = ("def _axiom_wrapper(c):\n"
           "    def read(a):\n"
           "        try:\n"
           "            return c.execute(a)\n"
           "        except Exception:\n"
           "            return None\n"
           "    def write(a, v):\n"
           "        try:\n"
           "            return c.execute(a, v)\n"
           "        except Exception:\n"
           "            return None\n"
           "    return read\n")
    hits = _blanket_except_sites(src, WRAPPER)
    assert len(hits) == 2, hits
    assert len({h.split("line ")[1].split(":")[0] for h in hits}) == 2


def test_code_outside_the_wrappers_is_not_the_connector_s_business():
    src = ("def unrelated():\n"
           "    try:\n"
           "        return 1\n"
           "    except Exception:\n"
           "        return 0\n")
    assert _blanket_except_sites(src, WRAPPER) == []


def test_the_retry_never_reverts_the_edit():
    """Reverting would turn a fidelity defect into a failed migration.

    Anchored on the call rather than on its formatting: the original split on
    ``"_blanket_except_sites(\\n"`` and broke the day the call fitted on one
    line, which says nothing about whether a revert was introduced.
    """
    body = open(os.path.join(ROOT, "smart_loader/agents/connector_agent.py"),
                encoding="utf-8").read()
    calls = [i for i in range(len(body))
             if body.startswith("swallows = _blanket_except_sites", i)]
    assert len(calls) == 1, "expected exactly one retry-loop call site"
    block = body[calls[0]:].split("except Exception as e:")[0]
    assert "working_source = edit_input_source" not in block
    assert "attempt >= 2" in body, "the retry must be bounded at one"
