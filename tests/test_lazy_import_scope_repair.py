"""A lazy import must reach every scope that uses the name it binds.

D1 seeds 1 and 2 both ended migration_success=N over this, with the source
reverted to the original::

    def read_holding_registers(self, address, count, unit=None):
        import modbus_tk.defines as cst          # bound HERE
        return self._execute(unit, cst.READ_HOLDING_REGISTERS, ...)

    def write_register(self, address, value, unit=None):
        return self._execute(unit, cst.WRITE_SINGLE_REGISTER, ...)
                                   # ^ NameError: name 'cst' is not defined

`_bind_wrapper_imports` repairs the same class of defect but only inside the
functions named as wrappers -- these are methods on a Connector-generated
adapter class, so they were skipped -- and it synthesises `import X`, which is
right only when the missing name IS a module name. No amount of guessing turns
`cst` into `import modbus_tk.defines as cst`.

The repair invents nothing: it reuses an import statement VERBATIM from
elsewhere in the same file, so the binding it adds is one the author already
wrote. A name bound nowhere in the file is a real unknown and is left alone.
"""
import ast
import os
import subprocess
import sys
import tempfile
import textwrap

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from smart_loader.agents.connector_agent import replicate_lazy_imports  # noqa: E402


def _s(code):
    return textwrap.dedent(code).lstrip()


def test_the_d1_case_is_repaired_with_the_files_own_statement():
    out, repairs = replicate_lazy_imports(_s("""
        class A:
            def read(self):
                import modbus_tk.defines as cst
                return cst.READ_HOLDING_REGISTERS

            def write(self):
                return cst.WRITE_SINGLE_REGISTER
    """))
    assert len(repairs) == 1 and "write" in repairs[0]
    assert out.count("import modbus_tk.defines as cst") == 2
    ast.parse(out)


def test_a_name_bound_nowhere_is_left_alone():
    """A real unknown belongs to the model, not to a guess."""
    src = _s("""
        def f():
            return mystery.thing
    """)
    assert replicate_lazy_imports(src) == (src, [])


def test_a_module_level_import_needs_no_repair():
    src = _s("""
        import modbus_tk.defines as cst

        def f():
            return cst.READ_HOLDING_REGISTERS
    """)
    assert replicate_lazy_imports(src) == (src, [])


def test_a_function_that_already_imports_it_is_untouched():
    src = _s("""
        def f():
            import json
            return json.dumps({})
    """)
    assert replicate_lazy_imports(src) == (src, [])


def test_a_docstring_is_not_demoted():
    """Inserting above the docstring silently costs the function its __doc__."""
    out, repairs = replicate_lazy_imports(_s('''
        class A:
            def read(self):
                import modbus_tk.defines as cst
                return cst.READ

            def write(self):
                """Write a register."""
                return cst.WRITE
    '''))
    assert repairs
    tree = ast.parse(out)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "write")
    assert ast.get_docstring(fn) == "Write a register."


def test_several_repairs_do_not_corrupt_each_others_line_numbers():
    out, repairs = replicate_lazy_imports(_s("""
        class A:
            def a(self):
                import modbus_tk.defines as cst
                return cst.X

            def b(self):
                return cst.Y

            def c(self):
                return cst.Z
    """))
    assert len(repairs) == 2
    ast.parse(out)
    assert out.count("import modbus_tk.defines as cst") == 3


def test_unparseable_source_is_returned_unchanged():
    assert replicate_lazy_imports("def broken(:") == ("def broken(:", [])


def test_parameters_and_locals_are_not_mistaken_for_missing_imports():
    src = _s("""
        def f(client):
            helper = object()
            return client.get, helper.x
    """)
    assert replicate_lazy_imports(src) == (src, [])


def test_the_repair_turns_a_nameerror_into_working_code():
    """Executed, not inspected: the exact shape D1 seeds 1 and 2 wrote."""
    broken = _s("""
        class A:
            def _execute(self, fc):
                return fc

            def read(self):
                import modbus_tk.defines as cst
                return self._execute(cst.READ_HOLDING_REGISTERS)

            def write(self):
                return self._execute(cst.WRITE_SINGLE_REGISTER)

        def build():
            return A()
    """)

    def runs(source):
        directory = tempfile.mkdtemp()
        with open(os.path.join(directory, "transport.py"), "w") as fh:
            fh.write(source)
        result = subprocess.run(
            [sys.executable, "-c",
             "import transport; a = transport.build(); a.read(); a.write()"],
            cwd=directory, capture_output=True, text=True, timeout=60)
        return result.returncode == 0, result.stderr

    ok_before, stderr = runs(broken)
    if "modbus_tk" in stderr and "No module named" in stderr:
        import pytest
        pytest.skip("modbus_tk not installed here")
    assert not ok_before and "cst" in stderr

    repaired, repairs = replicate_lazy_imports(broken)
    assert repairs
    ok_after, stderr_after = runs(repaired)
    assert ok_after, f"still fails: {stderr_after[-300:]}"


def test_the_repair_runs_after_the_import_rewrites():
    """Placed here after two wrong guesses, each a fix that never ran.

    It was first called on `working_source` before the ACI session, then on
    `final_source` inside it. Both are too early for the same reason:
    `import modbus_tk.defines as cst` is not in the file yet -- it arrives with
    `apply_import_replacements`. A repair that runs before that finds nothing
    to replicate and reports nothing, which is exactly what four D1 runs
    showed: every one failing on `Name "cst" is not defined` with zero "scope
    repair" lines in its log.

    So the ordering is pinned, not the call site alone: the repair must come
    after the import rewrite and be the last thing to touch the patch.
    """
    body = open(os.path.join(ROOT, "smart_loader/agents/connector_agent.py"),
                encoding="utf-8").read()
    repair = body.index("working_source, scope_fixes = replicate_lazy_imports")
    rewrite = body.index("apply_import_replacements(working_source")
    call_sites = body.index("working_source, applied = apply_call_site_rewrites")
    assert rewrite < repair, "the import rewrite must run first"
    assert call_sites < repair, "call-site rewrites must run first"

    import re as _re
    tail = body[repair:body.index('"patched_code": working_source,')]
    later = [ln.strip() for ln in tail.splitlines()
             if _re.search(r"^\s*working_source(,| =)", ln)
             and "scope_fixes" not in ln and "source_code" not in ln]
    assert later == [], f"something still rewrites the patch after it: {later}"


def test_the_repair_runs_on_the_edit_sessions_own_output():
    """It was wired onto `working_source` in the main flow, not `final_source`.

    That is before the ACI edit session produces the patch that actually
    ships, so on three D1 runs it repaired an earlier draft and never saw the
    `cst` the final wrappers used. All three failed on `Name "cst" is not
    defined` with a working fix sitting unused two functions away.

    A correct repair called at the wrong point is indistinguishable from no
    repair at all, so the call site is pinned.
    """
    body = open(os.path.join(ROOT, "smart_loader/agents/connector_agent.py"),
                encoding="utf-8").read()
    assert "replicate_lazy_imports(final_source)" in body, \
        "the scope repair must run on the edit session's output"
    # ...and before the checks that would otherwise reject the patch.
    assert (body.index("replicate_lazy_imports(final_source)")
            < body.index("_blanket_except_sites(final_source"))
