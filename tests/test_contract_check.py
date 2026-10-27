"""Public-surface regressions the equivalence oracle structurally cannot see.

Each case here was measured on a variant the oracle ACCEPTED and a blind judge
then flagged. Three programs, three shapes, one cause: the oracle exercises the
normal call path, so a rewrite can keep every recorded value identical while
changing the shape callers depend on.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from smart_loader.isomorph.contract_check import divergences  # noqa: E402


# ── D1: a plain attribute became a read-only property ───────────────────────

def test_a_plain_attribute_turned_read_only_is_caught():
    orig = ("class C:\n    def __init__(self, kp):\n        self.kp = kp\n")
    var = ("class C:\n    def __init__(self, kp):\n        self._kp = kp\n"
           "    @property\n    def kp(self):\n        return self._kp\n")
    found = divergences(orig, var)
    assert found and "kp" in found[0] and "read-only" in found[0]


def test_a_property_with_a_setter_is_fine():
    """Introducing a property is allowed; removing assignability is not."""
    orig = ("class C:\n    def __init__(self, kp):\n        self.kp = kp\n")
    var = ("class C:\n    def __init__(self, kp):\n        self._kp = kp\n"
           "    @property\n    def kp(self):\n        return self._kp\n"
           "    @kp.setter\n    def kp(self, v):\n        self._kp = v\n")
    assert divergences(orig, var) == []


def test_a_private_attribute_is_not_public_surface():
    orig = ("class C:\n    def __init__(self):\n        self._x = 1\n")
    var = ("class C:\n    def __init__(self):\n        self.__x = 1\n"
           "    @property\n    def _x(self):\n        return self.__x\n")
    assert divergences(orig, var) == []


# ── C2: a dataclass became a hand-written class ─────────────────────────────

def test_losing_dataclass_is_caught():
    orig = ("from dataclasses import dataclass\n\n@dataclass\n"
            "class Process:\n    level: float\n")
    var = ("class Process:\n    def __init__(self, level):\n"
           "        self.level = level\n")
    found = divergences(orig, var)
    assert found and "dataclass" in found[0]


def test_staying_a_dataclass_is_fine():
    orig = ("from dataclasses import dataclass\n\n@dataclass\n"
            "class Process:\n    level: float\n")
    var = ("from dataclasses import dataclass\n\n@dataclass(frozen=False)\n"
           "class Process:\n    level: float\n    def touch(self): pass\n")
    assert divergences(orig, var) == []


# ── D3: a lazy import was hoisted to module load ────────────────────────────

def test_hoisting_a_deferred_collaborator_is_caught_across_a_rename():
    """The migration changes the library NAME, so name-matching finds nothing.

    D3 deferred `paho` and the rewrite hoisted `aiomqtt`; what carries over is
    the property -- the original imported with its collaborator absent.
    """
    orig = ("import json\n\ndef _default():\n    import paho.mqtt.client as m\n"
            "    return m.Client()\n")
    var = ("import json\nimport aiomqtt\n\ndef _default():\n"
           "    return aiomqtt.Client()\n")
    found = divergences(orig, var)
    assert found and "aiomqtt" in found[0]


def test_an_import_hoisted_through_an_extracted_module_is_caught():
    """Putting it in a new module and importing that fails identically."""
    orig = ("import json\n\ndef _default():\n    import paho.mqtt.client as m\n"
            "    return m.Client()\n")
    var = ("import json\nfrom bridge import make\n\ndef _default():\n"
           "    return make()\n")
    found = divergences(orig, var,
                        {"bridge.py": "import aiomqtt\n\ndef make():\n"
                                      "    return aiomqtt.Client()\n"})
    assert found and "aiomqtt" in found[0]


def test_keeping_the_import_deferred_is_fine():
    orig = ("import json\n\ndef _default():\n    import paho.mqtt.client as m\n"
            "    return m.Client()\n")
    var = ("import json\n\ndef _default():\n    import aiomqtt\n"
           "    return aiomqtt.Client()\n")
    assert divergences(orig, var) == []


def test_stdlib_and_first_party_at_module_level_are_not_regressions():
    orig = ("import json\n\ndef _default():\n    import paho\n    return paho\n")
    var = ("import json\nimport asyncio\nimport threading\n"
           "from helper import go\n\ndef _default():\n    import aiomqtt\n"
           "    return go()\n")
    found = divergences(orig, var, {"helper.py": "def go(): return 1\n"})
    assert found == [], found


def test_a_program_with_no_deferred_imports_is_not_constrained():
    orig = "import json\n\ndef f():\n    return json\n"
    var = "import json\nimport httpx\n\ndef f():\n    return httpx\n"
    assert divergences(orig, var) == []


def test_unparseable_input_reports_nothing():
    assert divergences("def (", "x = 1") == []


def test_a_sibling_module_of_the_program_is_first_party():
    """Importing the program's own module is not hoisting a library.

    Measured on D3: a rewrite of sink.py that imports `estimator` -- a module
    sitting next to it in the same program -- read as hoisting a third-party
    collaborator, because first-party was derived only from the files the
    variant EXTRACTED and not from the program's own.
    """
    orig = ("import json\n\ndef _default():\n    import paho\n    return paho\n")
    var = ("import json\nfrom estimator import Estimator\n\n"
           "def _default():\n    import aiomqtt\n    return aiomqtt\n")
    assert divergences(orig, var) != []          # without siblings: flagged
    assert divergences(orig, var, siblings={"estimator"}) == []


def test_siblings_do_not_excuse_a_real_hoist():
    orig = ("import json\n\ndef _default():\n    import paho\n    return paho\n")
    var = ("import json\nimport aiomqtt\nfrom estimator import E\n\n"
           "def _default():\n    return aiomqtt\n")
    found = divergences(orig, var, siblings={"estimator"})
    assert found and "aiomqtt" in found[0]
