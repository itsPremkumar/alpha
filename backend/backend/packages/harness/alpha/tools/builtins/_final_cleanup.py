import os
import shutil

# Remove the stray double-path "backend/backend" that was created during this audit.
dub = "C:/Users/PREM KUMAR/Videos/alpha/backend/backend"
if os.path.isdir(dub):
    shutil.rmtree(dub)
    print("removed", dub)

# Remove leftover temp scripts under the workspace.
roots = [
    "backend/packages/harness/alpha/config",
    "backend/packages/harness/alpha/tools/builtins",
]
removed = []
for root in roots:
    if not os.path.isdir(root):
        continue
    for name in os.listdir(root):
        if name.startswith("_") and name.endswith(".py"):
            p = os.path.join(root, name)
            if name not in ("__init__.py",):
                os.remove(p)
                removed.append(p)
print("removed temp scripts:", removed if removed else "(none)")
