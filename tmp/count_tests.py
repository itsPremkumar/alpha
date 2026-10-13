import re, os
base = "C:/Users/PREM KUMAR/Videos/alpha"
files = [l.strip() for l in open("C:/Users/PREM KUMAR/Videos/alpha/tmp/testfiles.txt") if l.strip()]
tot = 0
nf = 0
big = []
for f in files:
    try:
        txt = open(os.path.join(base, f), encoding="utf-8", errors="replace").read()
    except Exception:
        continue
    n = len(re.findall(r"^\s*def test_", txt, re.M))
    if n:
        nf += 1
        tot += n
        big.append((n, f))
print("files_with_tests=%d total_test_funcs=%d" % (nf, tot))
for n, f in sorted(big, reverse=True)[:25]:
    print("%d\t%s" % (n, f))
