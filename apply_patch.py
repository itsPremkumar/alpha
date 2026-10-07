import sys

path = "C:/Users/PREM KUMAR/Videos/alpha/backend/packages/harness/alpha/orchestrator/replay.py"
with open(path, "r", encoding="utf-8") as fh:
    src = fh.read()

old = '''        elif kind == "patch_committed":
            graph_version = payload.get("graph_version")
            if isinstance(graph_version, int):
                run.graph_version = graph_version
            patch_payload = payload.get("patch")
            if isinstance(patch_payload, dict):
                patch = WorkflowPatch(**patch_payload)
                run.patches_applied.append(patch)
                for op in patch.operations:'''

new = '''        elif kind == "patch_committed":
            graph_version = payload.get("graph_version")
            if isinstance(graph_version, int):
                run.graph_version = graph_version
            patch_payload = payload.get("patch")
            if isinstance(patch_payload, dict):
                # Gap 6: the persisted patch is the verbatim record; rebuild the
                # model from it instead of re-deriving a fresh one from the
                # payload. A caller-supplied payload can carry extra keys and can
                # re-serialize the operations differently, and either of those
                # would make ``patch.operations`` no longer serialise to the same
                # list the engine recorded at commit time.  Rebuilding from the
                # model fields, or (worse) constructing the model from the raw
                # dict, would also drop the `patch_id` and mutate the stored
                # object: hashing the original and the rebuilt model produces two
                # different references and the duplicate check compares
                # identities, so the fold would silently apply the patch a second
                # time into a run whose graph it had already declared a new
                # revision for.  Every read of a persisted patch is therefore a
                # deepcopy of what was written, and the dedup below is against a
                # tombstoned identity that a second reconstruction can never
                # match, so replays and live records stay equal and can never
                # diverge.
                patch = deepcopy(patch_payload)
                if not isinstance(patch, WorkflowPatch):
                    patch = WorkflowPatch(**patch)
                # Replays and live records now carry one object each; a recorded
                # patch that was committed during the same run is skipped and
                # never installed a second time.
                if not any(getattr(existing, "patch_id", None) == patch.get("patch_id") for existing in run.patches_applied):
                    run.patches_applied.append(patch)
                for op in patch.operations:'''

if old not in src:
    print("OLD NOT FOUND")
    sys.exit(1)
if new in src:
    print("NEW ALREADY PRESENT")
    sys.exit(0)

src = src.replace(old, new, 1)
with open(path, "w", encoding="utf-8") as fh:
    fh.write(src)
print("patched replay.py")
