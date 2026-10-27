#!/usr/bin/env bash
# Run every program's test suite. Prefers pytest; falls back to each test file's
# built-in __main__ runner (dual-mode tests) when pytest is unavailable.
set -u
here="$(cd "$(dirname "$0")" && pwd)"
fail=0

for testfile in "$here"/*/test_*.py; do
    dir="$(dirname "$testfile")"
    name="$(basename "$dir")"
    echo "=== $name ==="
    if command -v pytest >/dev/null 2>&1; then
        ( cd "$dir" && pytest -q ) || fail=1
    else
        ( cd "$dir" && python3 "$(basename "$testfile")" ) || fail=1
    fi
done

echo
if [ "$fail" -eq 0 ]; then echo "ALL SUITES PASSED"; else echo "SOME SUITES FAILED"; fi
exit "$fail"
