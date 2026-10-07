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

shell_true = []
subprocess_calls = []
eval_exec = []
popen = []
for f in paths:
    try:
        txt = open(f, encoding='utf-8').read()
    except Exception:
        continue
    lines = txt.splitlines()
    for i, line in enumerate(lines, 1):
        if re.search(r'\bshell\s*=\s*True', line):
            shell_true.append((f, i, line.strip()))
        if re.search(r'\b(subprocess|os\.system|popen|Popen|Popen\w*\(|popen\w*\()', line):
            subprocess_calls.append((f, i, line.strip()))
        if re.search(r'\b(eval|exec)\s*\(', line):
            eval_exec.append((f, i, line.strip()))
        if re.search(r'\bpopen\s*\(', line):
            popen.append((f, i, line.strip()))

print("=== shell=True ===")
for h in shell_true:
    print(h)
print("\n=== subprocess/popen ===")
for h in subprocess_calls:
    print(h)
print("\n=== eval/exec/load ===")
for h in eval_exec:
    print(h)
print("\n=== popen ===")
for h in popen:
    print(h)
