import re
import pathlib

import yaml

PATS = [
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"AIza[0-9A-Za-z_\-]{20,}"),
    re.compile(r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{5,}"),
]
for d in ("installer", "build", ".devcontainer"):
    for p in pathlib.Path(d).rglob("*"):
        if not p.is_file() or any(x in {"node_modules", ".venv", "__pycache__"} for x in p.parts):
            continue
        try:
            t = p.read_text(encoding="utf-8")
        except Exception:
            continue
        for pat in PATS:
            m = pat.search(t)
            if m:
                print(f"{p}: {pat.pattern} -> {m.group(0)[:40]!r}")

c = yaml.safe_load(open(".devcontainer/devcontainer.json", encoding="utf-8"))
print("image=", repr(c.get("image")))
print("postCreateCommand=", repr(c.get("postCreateCommand")))
