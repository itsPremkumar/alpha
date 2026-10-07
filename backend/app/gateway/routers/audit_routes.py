import os, ast

base = "."
files = sorted(f for f in os.listdir('.') if f.endswith('.py'))
print("TOTAL ROUTER FILES:", len(files))

unauthorized_mutating = []

for fname in files:
    with open(fname, encoding='utf-8') as fh:
        src = fh.read()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        if node.name.startswith('test_'):
            continue
        decorators = []
        for dec in node.decorator_list:
            if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute) and dec.func.attr == 'route':
                path = None
                methods = None
                for arg in dec.args:
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        path = arg.value
                    if isinstance(arg, ast.keyword) and arg.arg == 'methods':
                        methods = arg.value
                if path is None:
                    continue
                methods_str = None
                if methods is not None:
                    methods_str = [m.value for m in methods.elts if isinstance(m, ast.Constant)]
                decorators.append(('route', path, methods_str))
            elif isinstance(dec, ast.Name) and dec.id in ('require_auth', 'require_permission', 'require_admin_user', 'require_session_user'):
                decorators.append(('decorator', dec.id, None))
            elif isinstance(dec, ast.Attribute) and dec.attr in ('require_auth', 'require_permission', 'require_admin_user', 'require_session_user'):
                decorators.append(('decorator', dec.attr, None))
        if not decorators:
            continue
        # Check if this is a mutating route
        is_mutating = False
        for d in decorators:
            if d[0] == 'route' and d[1] and '*' not in d[1]:
                path = d[1]
                if d[2]:
                    if any(m in ('POST','PUT','PATCH','DELETE') for m in d[2]):
                        is_mutating = True
                else:
                    is_mutating = True
        if is_mutating:
            # Find auth decorators
            auth_decs = [d for d in decorators if d[0] == 'decorator' and d[1] in ('require_auth', 'require_permission', 'require_admin_user', 'require_session_user')]
            if not auth_decs:
                unauthorized_mutating.append(f"{fname}: {node.name}")

print("\nUNAUTHORIZED MUTATING ROUTES:")
for r in unauthorized_mutating:
    print(" ", r)
