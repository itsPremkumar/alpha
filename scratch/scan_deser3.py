import os, glob, re
scope = ['backend/packages/harness/alpha/tools','backend/packages/harness/alpha/sandbox','backend/packages/harness/alpha/security','backend/packages/harness/alpha/safety','backend/packages/harness/alpha/guardrails','backend/packages/harness/alpha/authz','backend/packages/harness/alpha/egress','backend/packages/harness/alpha/policy','backend/packages/harness/alpha/computer_use','backend/app/gateway/auth']
scratch = os.path.abspath('scratch') + os.sep
for s in scope:
    for f in sorted(glob.glob(s+'/.py', recursive=True)):
        ap = os.path.abspath(f)
        if ap.startswith(scratch):
            continue
        try:
            txt = open(f, encoding='utf-8').read()
        except Exception:
            continue
        for i, l in enumerate(txt.splitlines(), 1):
            if re.search(r'yaml\.load|pickle\.(load|loads)|shelve|marshal\.load|base64\.(b64decode|decodebytes)', l):
                print(f, i, l.strip())
