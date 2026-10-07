import re

targets = [
 "backend/packages/harness/alpha/tools/builtins/bot_roster_tool.py",
 "backend/packages/harness/alpha/tools/builtins/visual_verification_tool.py",
 "backend/packages/harness/alpha/tools/script_bridge/stubgen.py",
 "backend/packages/harness/alpha/sandbox/computer_use.py",
 "backend/packages/harness/alpha/sandbox/tools.py",
 "backend/packages/harness/alpha/security/autonomy/classifier.py",
 "backend/packages/harness/alpha/tools/builtins/ast_grep_tool.py",
 "backend/packages/harness/alpha/tools/builtins/keyless_web_search_tool.py",
 "backend/packages/harness/alpha/security/memory_redaction.py",
]
pat = re.compile(r'\b(re\.(?:match|search|fullmatch))\s*\(', re.IGNORECASE)
strpat = re.compile(r"r?['\"](.*?)['\"]")
for p in targets:
    try:
        lines = open(p, encoding="utf-8", errors="ignore").read().splitlines()
    except Exception as e:
        print("ERR", p, e); continue
    for i, line in enumerate(lines, 1):
        if not pat.search(line): continue
        for s in strpat.finditer(line):
            pat2 = s.group(1)
            if '$' in pat2 or 'MULTILINE' in pat2 or 're.MULTILINE' in pat2 or 're.M' in pat2:
                print(f"{p}:{i}: {line.strip()}  [pat={pat2[:90]}]")
