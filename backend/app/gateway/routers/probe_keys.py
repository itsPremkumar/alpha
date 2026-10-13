import ast

path = "/c/Users/PREM KUMAR/Videos/alpha/backend/app/gateway/services.py"
src = open(path, encoding="utf-8").read()
t = ast.parse(src)
for i, item in enumerate(t.body):
    if isinstance(item, ast.Assign):
        for tg in item.targets:
            if isinstance(tg, ast.Name) and tg.id in (
                "_CONTEXT_CONFIGURABLE_KEYS",
                "_CONTEXT_INTERNAL_CALLER_KEYS",
                "_CONTEXT_RUNTIME_ONLY_KEYS",
            ):
                print(f"--- line {item.lineno} ---")
                print(ast.get_source_segment(src, item))
