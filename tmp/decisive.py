import ast, pathlib, sys

ROOT = pathlib.Path("C:/Users/PREM KUMAR/Videos/alpha")
TESTS = ROOT / "backend" / "tests"
PROD_ROOTS = [ROOT / "backend" / "packages" / "harness" / "alpha", ROOT / "backend" / "app"]

# (dotted module, function name, production description, where it is used)
SAMPLES = [
    ("alpha.runtime.store._sqlite_utils", "ensure_sqlite_parent_dir", "sync SQLite dir creation (Blockbuster gate target)"),
    ("alpha.runtime.store._sqlite_utils", "apply_sqlite_connect_pragmas", "async aiosqlite pragma setup"),
    ("alpha.runtime.store.provider", "ensure_sqlite_parent_dir", "sync dir creation, sync store provider path"),
    ("alpha.runtime.checkpointer.provider", "ensure_sqlite_parent_dir", "checkpointer sync path"),
    ("alpha.agents.middlewares.grounding_middleware", "_gate", "tool gating decision"),
    ("alpha.agents.middlewares.grounding_middleware", "_blocked_result", "blocked tool result builder"),
    ("alpha.runtime.escalation", "record_failure", "escalation record"),
    ("alpha.runtime.runs.manager", "run_agent", "agent run runner"),
    ("alpha.agents.middlewares.summarization_middleware", "notify_context_compacted", "compaction observer hook"),
    ("alpha.apex.store.ApexStore", "set_state", "APEX session state write"),
    ("alpha.memory", "recall_composition", "cognitive memory read-side composition"),
    ("alpha.testing.mutation_fuzzer", "main", "mutation fuzzing entry point"),
    ("alpha.synthesis.speculative_tournament", "main", "speculative synthesis tournament entry point"),
]

def test_touch(fn_name):
    """Return (test_files_importing_or_calling, would_break_if_deleted)."""
    imports = set()
    calls = set()
    for t in TESTS.rglob("*.py"):
        try:
            src = t.read_text(encoding="utf-8", errors="ignore")
            tree = ast.parse(src)
        except Exception:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and node.module.endswith("." + fn_name):
                imports.add(t.relative_to(ROOT).as_posix())
            elif isinstance(node, ast.Import):
                for a in node.names:
                    if a.name == fn_name or a.name.endswith("." + fn_name):
                        imports.add(t.relative_to(ROOT).as_posix())
            elif isinstance(node, ast.Call):
                f = node.func
                if isinstance(f, ast.Name) and f.id == fn_name:
                    calls.add(t.relative_to(ROOT).as_posix())
                elif isinstance(f, ast.Attribute) and f.attr == fn_name:
                    calls.add(t.relative_to(ROOT).as_posix())
    return imports, calls

def prod_refs(fn_name):
    out = set()
    for base in PROD_ROOTS:
        if not base.exists():
            continue
        for p in base.rglob("*.py"):
            try:
                src = p.read_text(encoding="utf-8", errors="ignore")
                tree = ast.parse(src)
            except Exception:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    f = node.func
                    if isinstance(f, ast.Name) and f.id == fn_name:
                        out.add(("Call", p.relative_to(ROOT).as_posix()))
                    elif isinstance(f, ast.Attribute) and f.attr == fn_name:
                        out.add(("Attr", p.relative_to(ROOT).as_posix()))
    return sorted(out)

if __name__ == "__main__":
    any_broken = 0
    for mod, fn, desc in SAMPLES:
        im, cl = test_touch(fn)
        pr = prod_refs(fn)
        # A test breaks if deleting the function makes it uncallable/import-fails:
        #  - any test that IS the module containing fn (collection of that test file breaks)
        #  - any test that calls/imports the fn
        broken_by = sorted(set(im) | set(cl))
        status = "TESTS-BREAK" if broken_by else "NO-TEST-IMPACT"
        if broken_by:
            any_broken += 1
        print(f"[{status}] {fn} ({desc})")
        print(f"    test touch: {len(broken_by)} -> {broken_by[:8]}")
        print(f"    prod refs: {len(pr)}")
        for x in pr[:10]:
            print(f"      {x[0]} {x[1]}")
        print()
    print(f"SUMMARY: {any_broken}/{len(SAMPLES)} sampled functions have at least one test that fails if deleted")
