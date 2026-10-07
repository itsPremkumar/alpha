import os, glob, re

gw = "C:/Users/PREM KUMAR/Videos/alpha/backend/app/gateway/routers"
routers = sorted(glob.glob(gw + "/*.py"))
print("TOTAL ROUTERS:", len(routers))
for f in routers:
    src = open(f, encoding="utf-8").read()
    lines = src.splitlines()
    for i, line in enumerate(lines, 1):
        if re.search(r'@router\.(get|post|put|patch|delete|head|options)', line):
            decs = []
            j = i - 2
            while j >= 0 and re.match(r'@[\w<>, .]+', lines[j].strip()):
                decs.append(lines[j].strip())
                j -= 1
            decs = decs[::-1]
            method = re.findall(r'@router\.(get|post|put|patch|delete|head|options)', line)[0].upper()
            guard = any("require_auth" in d or "require_permission" in d or "require_admin_user" in d or "require_session_source" in d for d in decs)
            if method in ("POST","PUT","PATCH","DELETE"):
                print(f"{method} {os.path.basename(f)}:{i} guarded={guard}")
                for d in decs[:6]:
                    if "require" in d:
                        print("      ", d)
