"""Record the reference behavior of the pristine corpus into golden.json.

Run this ONCE on the un-diversified modules. The resulting golden.json is your
regression oracle: `test_corpus.py` compares each (possibly diversified) module
back against it.

    python make_golden.py
"""

import json
import oracle


def build():
    data = {}
    for name in oracle.MODULES:
        m = oracle.load(name)
        data[name] = {
            "scenario": [list(x) for x in m.run_scenario()],
            "probes": {pn: fn(m) for pn, fn in oracle.PROBES[name].items()},
        }
    return data


if __name__ == "__main__":
    with open("golden.json", "w") as f:
        json.dump(build(), f, indent=1)
    print("wrote golden.json for:", ", ".join(oracle.MODULES))
