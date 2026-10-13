import os
import sys

os.environ.setdefault("PYTHONPATH", "/c/Users/PREM KUMAR/Videos/alpha/backend")
sys.argv = ["_audit_routes"]
from app import app  # noqa: E402


def main():
    flat = []

    def walk(prefix, routes):
        for r in routes:
            p = prefix + str(getattr(r, "path", ""))
            if hasattr(r, "routes"):
                walk(p, r.routes)
            else:
                methods = getattr(r, "methods", None) or []
                name = getattr(r, "name", None)
                if methods:
                    flat.append((sorted(methods), p, name))

    walk("", app.routes)
    for methods, p, name in sorted(flat, key=lambda x: x[1]):
        print(f"{','.join(methods):20s} {p:60s} {name}")


if __name__ == "__main__":
    main()
