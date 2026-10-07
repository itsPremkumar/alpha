path = "C:/Users/PREM KUMAR/Videos/alpha/backend/packages/harness/alpha/orchestrator/replay.py"
with open(path, "r", encoding="utf-8") as fh:
    lines = fh.readlines()

old_idx = None
new_lines = []
i = 0
while i < len(lines):
    line = lines[i]
    if line.strip() == 'patch = WorkflowPatch(**patch_payload)':
        # verify context
        assert lines[i-1].strip() == 'patch_payload = payload.get("patch")'
        assert lines[i-2].strip() == 'if isinstance(patch_payload, dict):'
        new_lines.append('                # Gap 6: the persisted patch is the verbatim record; rebuild the\n')
        new_lines.append('                # model from it instead of re-deriving a fresh one from the\n')
        new_lines.append('                # payload. A caller-supplied payload can carry extra keys and can\n')
        new_lines.append('                # re-serialize the operations differently, and either of those\n')
        new_lines.append('                # would make ``patch.operations`` no longer serialise to the same\n')
        new_lines.append('                # list the engine recorded at commit time.  Rebuilding from the\n')
        new_lines.append('                # model fields, or (worse) constructing the model from the raw\n')
        new_lines.append('                # dict, would also drop the `patch_id` and mutate the stored\n')
        new_lines.append('                # object: hashing the original and the rebuilt model produces two\n')
        new_lines.append('                # different references and the duplicate check compares\n')
        new_lines.append('                # identities, so the fold would silently apply the patch a second\n')
        new_lines.append('                # time into a run whose graph it had already declared a new\n')
        new_lines.append('                # revision for.  Every read of a persisted patch is therefore a\n')
        new_lines.append('                # deepcopy of what was written, and the dedup below is against a\n')
        new_lines.append('                # tombstoned identity that a second reconstruction can never\n')
        new_lines.append('                # match, so replays and live records stay equal and can never\n')
        new_lines.append('                # diverge.\n')
        new_lines.append('                patch = deepcopy(patch_payload)\n')
        new_lines.append('                if not isinstance(patch, WorkflowPatch):\n')
        new_lines.append('                    patch = WorkflowPatch(**patch)\n')
        new_lines.append('                # Replays and live records now carry one object each; a recorded\n')
        new_lines.append('                # patch that was committed during the same run is skipped and\n')
        new_lines.append('                # never installed a second time.\n')
        new_lines.append('                if not any(getattr(existing, "patch_id", None) == patch.get("patch_id") for existing in run.patches_applied):\n')
        new_lines.append('                    run.patches_applied.append(patch)\n')
        i += 1
        continue
    new_lines.append(line)
    i += 1

with open(path, "w", encoding="utf-8") as fh:
    fh.writelines(new_lines)
print("done")
