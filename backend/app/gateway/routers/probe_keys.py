"""Print the run-context key definitions from `services.py` as source segments.

Hand-run diagnostic, never imported at runtime: the module body performs no
filesystem work, so importing every router module (as the thread-id route
contract sweep does) stays side-effect free.
"""

import ast
from pathlib import Path

SERVICES_PATH = Path(__file__).resolve().parent.parent / "services.py"


def main() -> None:
    src = SERVICES_PATH.read_text(encoding="utf-8")
    t = ast.parse(src)
    for item in t.body:
        if isinstance(item, ast.Assign):
            for tg in item.targets:
                if isinstance(tg, ast.Name) and tg.id in (
                    "_CONTEXT_CONFIGURABLE_KEYS",
                    "_CONTEXT_INTERNAL_CALLER_KEYS",
                    "_CONTEXT_RUNTIME_ONLY_KEYS",
                ):
                    print(f"--- line {item.lineno} ---")
                    print(ast.get_source_segment(src, item))


if __name__ == "__main__":
    main()
