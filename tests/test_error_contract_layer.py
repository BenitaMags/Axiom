"""Layer 7 reports error contracts the migration changed, and nothing else.

The clone this exists for scored a clean sweep on every other layer -- runs,
4/4 tests, oracle Y, layer 6 OK -- while converting paho's ``ValueError:
Publish topic cannot contain wildcards`` into a return code that ``gateway.py``
discards. A telemetry gateway that failed loudly began silently dropping data,
and nothing in the pipeline could see it.

The negative cases matter as much as the positive ones. This layer reads every
clone in the corpus, so a rule that fires on ordinary adapter code would drown
the real findings.
"""
import glob
import os
import sys
import textwrap

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "equivalence"))

from error_contract import check, divergences, handler_signatures  # noqa: E402

CORPUS = os.path.expanduser("~/Downloads/evaluation")


def _pair(tmp_path, before: str, after: str, name: str = "m.py"):
    original = tmp_path / "orig"; clone = tmp_path / "clone"
    original.mkdir(); clone.mkdir()
    (original / name).write_text(textwrap.dedent(before), encoding="utf-8")
    (clone / name).write_text(textwrap.dedent(after), encoding="utf-8")
    return str(original), str(clone), [name]


# ── The defect ───────────────────────────────────────────────────────────────

def test_added_swallowing_handler_is_a_defect(tmp_path):
    """The D3 shape: no handler before, catch-and-return after."""
    o, c, files = _pair(tmp_path, """
        def publish(client, topic, payload):
            return client.publish(topic, payload)
    """, """
        def publish(client, topic, payload):
            try:
                client.publish(topic, payload)
                return Result(0)
            except Exception:
                return Result(1)
    """)
    verdict, detail = check(o, c, files)
    assert verdict == "DEFECT"
    assert "publish" in detail and "Exception" in detail


def test_added_bare_except_is_a_defect(tmp_path):
    o, c, files = _pair(tmp_path, """
        def send(client):
            return client.send()
    """, """
        def send(client):
            try:
                return client.send()
            except:
                return None
    """)
    assert check(o, c, files)[0] == "DEFECT"


# ── What must NOT be reported ────────────────────────────────────────────────

def test_handler_that_reraises_is_fine(tmp_path):
    """Wrapping to add context is not swallowing, as long as it propagates."""
    o, c, files = _pair(tmp_path, """
        def send(client):
            return client.send()
    """, """
        def send(client):
            try:
                return client.send()
            except ValueError as exc:
                raise RuntimeError("send failed") from exc
    """)
    assert check(o, c, files)[0] == "OK"


def test_handler_the_original_already_had_is_fine(tmp_path):
    """Differential. A faithful copy of pre-existing behaviour is not a defect."""
    body = """
        def send(client):
            try:
                return client.send()
            except Exception:
                return None
    """
    o, c, files = _pair(tmp_path, body, body)
    assert check(o, c, files)[0] == "OK"


def test_swallowing_in_dunder_del_is_fine(tmp_path):
    """Python ignores exceptions in a destructor; guarding there is idiomatic.

    Four clones were reported solely for this before the exclusion existed.
    """
    o, c, files = _pair(tmp_path, """
        class C:
            pass
    """, """
        class C:
            def __del__(self):
                try:
                    self._client.disconnect()
                except Exception:
                    pass
    """)
    assert check(o, c, files)[0] == "OK"


def test_close_is_not_excused(tmp_path):
    """A close() that hides a flush failure loses data."""
    o, c, files = _pair(tmp_path, """
        class C:
            def close(self):
                self._client.close()
    """, """
        class C:
            def close(self):
                try:
                    self._client.close()
                except Exception:
                    pass
    """)
    assert check(o, c, files)[0] == "DEFECT"


def test_swallowing_a_conversion_error_is_a_defect(tmp_path):
    """Not only collaborator calls. A silently defaulted parse is the same bug.

    This case was written expecting OK, on the theory that ``int()`` is not a
    collaborator. That was wrong: the original raises ValueError on malformed
    input and the clone returns 0, so a caller that relied on the error now
    gets a plausible-looking number. On a timestamp or CRC parser that is
    exactly the defect this layer exists to find.
    """
    o, c, files = _pair(tmp_path, """
        def parse(text):
            return int(text)
    """, """
        def parse(text):
            try:
                return int(text)
            except ValueError:
                return 0
    """)
    assert check(o, c, files)[0] == "DEFECT"


def test_try_around_a_call_free_body_is_not_reported(tmp_path):
    """Nothing in the guarded body can fail in a way worth reporting."""
    o, c, files = _pair(tmp_path, """
        def pick(values):
            return values[0]
    """, """
        def pick(values):
            try:
                return values[0]
            except IndexError:
                return None
    """)
    assert check(o, c, files)[0] == "OK"


def test_renamed_locals_do_not_create_a_divergence(tmp_path):
    """The LSA renames locals; signatures must not depend on them."""
    o, c, files = _pair(tmp_path, """
        def send(client):
            try:
                result = client.send()
                return result
            except Exception:
                return None
    """, """
        def send(client):
            try:
                _spt_r1 = client.send()
                return _spt_r1
            except Exception:
                return None
    """)
    assert check(o, c, files)[0] == "OK"


def test_syntax_error_yields_no_signatures(tmp_path):
    assert handler_signatures("def f(\n") == set()


# ── Against the corpus ───────────────────────────────────────────────────────

@pytest.mark.skipif(not os.path.isdir(CORPUS), reason="corpus not present")
def test_every_pristine_original_passes_against_itself():
    """A program compared with itself has changed no contract, by definition."""
    from smart_loader.eval.proteus_harness import discover_programs
    for program in discover_programs(CORPUS):
        verdict, detail = check(program.path, program.path, program.source_files)
        assert verdict == "OK", f"{program.name}: {detail}"


@pytest.mark.skipif(
    not glob.glob("/tmp/gate_clones/proteus_full/D3_telemetry_gateway_run2/*.py"),
    reason="probe clone not present")
def test_the_clone_the_judge_rated_major_is_caught():
    from smart_loader.eval.proteus_harness import discover_programs
    program = {p.name: p for p in discover_programs(CORPUS)}["D3_telemetry_gateway"]
    found = divergences(program.path,
                        "/tmp/gate_clones/proteus_full/D3_telemetry_gateway_run2",
                        program.source_files)
    assert found, "layer 7 must catch what the blind judge caught"
    assert any(f["file"] == "sink.py" and "publish" in f["function"] for f in found)


# ── modules the clone ADDED ──────────────────────────────────────────────────

def _prog(tmp_path, files):
    d = tmp_path / "orig"
    d.mkdir()
    for n, b in files.items():
        (d / n).write_text(b)
    return str(d)


def _clone(tmp_path, files):
    d = tmp_path / "clone"
    d.mkdir()
    for n, b in files.items():
        (d / n).write_text(b)
    return str(d)


def test_a_swallow_in_an_added_module_is_caught(tmp_path):
    """The file-by-name loop cannot see a module with no original counterpart.

    Measured on a D1 clone whose Connector adapter was restructured into
    ``modbus_bridge.py``: layer 7 reported OK while a blind judge rated the
    clone MAJOR for the two ``except Exception: return`` handlers inside it.
    """
    orig = _prog(tmp_path, {"t.py": "def read(c):\n    return c.get()\n"})
    clone = _clone(tmp_path, {
        "t.py": "from bridge import read\n",
        "bridge.py": ("def read(c):\n    try:\n        return c.get()\n"
                      "    except Exception:\n        return None\n"),
    })
    found = divergences(orig, clone, ["t.py"])
    assert found, "a new swallowing handler in an added module must be reported"
    assert found[0]["file"] == "bridge.py"
    assert "ADDED by the migration" in found[0]["detail"]


def test_a_handler_that_merely_moved_is_not_reported(tmp_path):
    """Relocating code is a restructuring, not a fidelity defect.

    Flagging it would penalise exactly the variants that extract a module,
    which is the shape this project is trying to produce.
    """
    handler = ("def read(c):\n    try:\n        return c.get()\n"
               "    except ValueError:\n        return None\n")
    orig = _prog(tmp_path, {"t.py": handler})
    clone = _clone(tmp_path, {"t.py": "from bridge import read\n",
                              "bridge.py": handler})
    assert divergences(orig, clone, ["t.py"]) == []


def test_an_added_module_that_re_raises_is_fine(tmp_path):
    orig = _prog(tmp_path, {"t.py": "def read(c):\n    return c.get()\n"})
    clone = _clone(tmp_path, {
        "t.py": "from bridge import read\n",
        "bridge.py": ("def read(c):\n    try:\n        return c.get()\n"
                      "    except Exception:\n        raise\n"),
    })
    assert divergences(orig, clone, ["t.py"]) == []


def test_a_narrow_translating_handler_in_an_added_module_is_not_a_defect(tmp_path):
    """The faithful adapter pattern, which an earlier version of this pass broke.

    When the ORIGINAL library reports a failure by RETURN VALUE rather than by
    raising -- pymodbus hands back an object whose ``.isError()`` the call site
    checks -- an adapter that catches the target's own error and returns that
    same status reproduces the original contract exactly.

    A blind judge drew this line on two real D1 adapters: ``except Exception``
    was MAJOR ("every exception including socket timeouts and connection loss
    is swallowed") and ``except ModbusError`` drew no comment at all. Widening
    this pass to see added modules made it flag the faithful one until the
    narrowness rule was added.
    """
    orig = _prog(tmp_path, {"t.py": "def read(c):\n    return c.get()\n"})
    clone = _clone(tmp_path, {
        "t.py": "from bridge import read\n",
        "bridge.py": ("import lib\n\ndef read(c):\n    try:\n"
                      "        return _R(c.get())\n"
                      "    except lib.ProtocolError as exc:\n"
                      "        return _R(error=exc)\n"),
    })
    assert divergences(orig, clone, ["t.py"]) == []


def test_a_broad_swallow_in_an_added_module_is_still_a_defect(tmp_path):
    """The narrowness rule must not let `except Exception` through."""
    orig = _prog(tmp_path, {"t.py": "def read(c):\n    return c.get()\n"})
    clone = _clone(tmp_path, {
        "t.py": "from bridge import read\n",
        "bridge.py": ("def read(c):\n    try:\n        return _R(c.get())\n"
                      "    except Exception as exc:\n        return _R(error=exc)\n"),
    })
    found = divergences(orig, clone, ["t.py"])
    assert found and found[0]["file"] == "bridge.py"
