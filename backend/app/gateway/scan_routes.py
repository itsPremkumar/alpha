import ast, glob, os

GATEWAY = 'backend/app/gateway/routers'
REQUIRES = ('require_auth', 'require_permission', 'require_admin_user', 'require_session_source')
MUTATING = {'POST', 'PUT', 'PATCH', 'DELETE'}
ROUTE_KINDS = ('@router.get', '@router.post', '@router.put', '@router.patch', '@router.delete', '@router.websocket', '@router.option')

def get_routes(root):
    results = []
    for f in sorted(glob.glob(os.path.join(root, '*.py'))):
        src = open(f, encoding='utf-8').read()
        try:
            tree = ast.parse(src)
        except SyntaxError as e:
            results.append((f, 'SYNTAX_ERROR', str(e)))
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                decs = [ast.unparse(d) for d in node.decorator_list]
                router_dec = next((d for d in decs if d.startswith('@router.')), None)
                if not router_dec:
                    continue
                method = router_dec.split('.')[1].lstrip('(').rstrip(')')
                dep_names = [d.split('(')[0].lstrip('@') for d in decs if d.startswith('@') and not d.startswith('@router.')]
                has_auth = any(d in REQUIRES for d in decs)
                has_dep = any(d == 'Depends(' for d in decs)
                has_router_deps = any('dependencies=[' in d for d in decs)
                results.append((f, method, node.name, has_auth, has_dep, has_router_deps, decs[:8]))
    return results

rows = get_routes(GATEWAY)
print(f'TOTAL ROUTES: {len(rows)}')
print(f'ROUTES WITH AUTH DECORATOR: {sum(1 for r in rows if r[3])}')
print(f'ROUTES WITH DEPENDENCIES= : {sum(1 for r in rows if r[5])}')
print(f'ROUTES WITH ANY AUTH: {sum(1 for r in rows if r[3] or r[4] or r[5])}')

print('\n--- MUTATING ROUTES without auth ---')
for f, method, name, has_auth, has_dep, has_router_deps, decs in rows:
    if method in MUTATING and not (has_auth or has_dep or has_router_deps):
        print(f'  [{method}] {os.path.basename(f)}:{name}  (decs={decs})')

print('\n--- ROUTE COUNT PER FILE ---')
cnt = {}
for f, method, name, has_auth, has_dep, has_router_deps, decs in rows:
    cnt[f] = cnt.get(f, 0) + 1
for f, n in sorted(cnt.items()):
    print(f'  {os.path.basename(f)}: {n} routes')
