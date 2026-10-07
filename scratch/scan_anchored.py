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
paths = []
for s in scope:
    for f in sorted(glob.glob(s + '/**/*.py', recursive=True)):
        paths.append(f)

# string literal with optional r/b/raw prefix
lit = re.compile(r"""(
    (?:r|b|rb|br)\s*['\"]
    |['\"]
)([^'\"\n\\]*(?:\\.[^'\"\n\\]*)*)(['\"])""", re.VERBOSE)

def extract_literals(line):
    return lit.findall(line)

def literal_value(m):
    body = m[1]
    return body

def unquote(s):
    # remove surrounding quotes; handle \"
    return s

hits = []
multiline_hits = []
for f in paths:
    try:
        txt = open(f, encoding='utf-8').read()
    except Exception:
        continue
    lines = txt.splitlines()
    for i, line in enumerate(lines, 1):
        for m in extract_literals(line):
            body = m[1]
            # skip byte literals and non-strings
            if body.startswith(('b"', 'b\'', 'u"', 'u\'', 'B"', 'B\'', 'f"', 'f\'', 'F"', 'F\'')):
                continue
            if body.endswith('$') and not body.endswith('\\$'):
                hits.append((f, i, line.strip()))
        if 're.MULTILINE' in line or 're.M' in line:
            multiline_hits.append((f, i, line.strip()))
        for meth in ('re.match(', 're.search(', 're.fullmatch('):
            if meth in line:
                hits.append((f, i, 'CALL:' + line.strip()))

print("=== $-anchored literals near re.* calls ===")
for h in hits:
    print(h)
print("\n=== lines containing re.MULTILINE/re.M ===")
for h in multiline_hits:
    print(h)
print("\n=== re.* calls ===")
for f in paths:
    try:
        txt = open(f, encoding='utf-8').read()
    except Exception:
        continue
    for i, line in enumerate(txt.splitlines(), 1):
        if re.search(r'\bre\.(match|search|fullmatch)\(', line):
            print((f, i, line.strip()))
