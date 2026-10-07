import os, glob, re
scope = [
    'backend/packages/harness/alpha/tools',
    'backend/packages/harness/alpha/sandbox',
    'backend/packages/harness/alpha/security',
    'backend/packages/harness/alpha/safety',
    'backend/packages/harness/alpha/guardrails',
    'backend/packages/harness/alpha/authz',
    'backend/packages/harness/alpha/egress',
    'backend/packages/harness/alpha/policy',
    'backend/packages/harness/alpha/computer_use',
    'backend/app/gateway/auth',
]
# exclude scratch and venv
scratch_prefix = os.path.abspath('scratch') + os.sep
paths = []
for s in scope:
    for f in sorted(glob.glob(s + '/**/*.py', recursive=True)):
        ap = os.path.abspath(f)
        if ap.startswith(scratch_prefix) or ap.endswith('__pycache__' + os.sep):
            continue
        paths.append(ap)

lit = re.compile(r"""(
    (?:r|b|rb|br)\s*['\"]
    |['\"]
)([^'\"\n\\]*(?:\\.[^'\"\n\\]*)*)(['\"])""", re.VERBOSE)

def is_nonstring(m):
    body = m[1]
    return body.startswith(('b"', 'b\'', 'u"', 'u\'', 'B"', 'B\'', 'f"', 'f\'', 'F"', 'F\'', 'rb', 'br'))

results = []
call_results = []
for f in paths:
    try:
        txt = open(f, encoding='utf-8').read()
    except Exception:
        continue
    lines = txt.splitlines()
    for i, line in enumerate(lines, 1):
        if re.search(r'\bre\.(match|search|fullmatch)\(', line):
            call_results.append((f, i, 'CALL', line.strip()))
        for m in lit.findall(line):
            body = m[1]
            if is_nonstring(m):
                continue
            if body.endswith('$'):
                results.append((f, i, 'LIT-END$', line.strip()))

print("=== $ anchored literals ===")
for h in results:
    print(h)
print("\n=== re.* calls ===")
for h in call_results:
    print(h)
