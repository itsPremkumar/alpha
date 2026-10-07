"""Apply the budget double-emission fix to runtime.py."""
path = "backend/packages/harness/alpha/workflow/runtime.py"
with open(path, encoding="utf-8") as f:
    src = f.read()

old = '''        if run.budget_limit is not None and run.tokens_consumed >= run.budget_limit:
            reason = f"Workflow budget exhausted: {run.tokens_consumed}/{run.budget_limit} tokens consumed."
            self._exhaust_budget(run, reason)
            self.events.emit(
                "workflow_budget_exhausted",
                run.run_id,
                reason=reason,
                tokens_consumed=run.tokens_consumed,
                budget_limit=run.budget_limit,
                failed_nodes=sorted(set(run.failed_nodes)),
            )
            return run'''

new = '''        if run.budget_limit is not None and run.tokens_consumed >= run.budget_limit:
            reason = f"Workflow budget exhausted: {run.tokens_consumed}/{run.budget_limit} tokens consumed."
            # _exhaust_budget journals exactly ONE ``workflow_budget_exhausted``
            # event (and refuses a second when already terminal), so the direct
            # emit below is a duplicate and must not fire.
            self._exhaust_budget(run, reason)
            return run'''

assert old in src, "old block not found"
src = src.replace(old, new, 1)
with open(path, "w", encoding="utf-8") as f:
    f.write(src)
print("patched OK; new block:")
i = src.find("if run.budget_limit is not None")
print(src[i:i+700])
