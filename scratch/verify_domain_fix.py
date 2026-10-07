import sys
sys.path.insert(0, "backend/packages/harness/alpha")
import alpha.packages.harness.alpha.egress.policy as p

cases = [
    "example.com",
    "evil.com\n",
    "evil.com\r\n",
    "a-b.example.com",
    "EXAMPLE.COM",
    "bad",
    "example..com",
    "localhost",
    "123.45.67.89",
    "xn--",
]
for t in cases:
    try:
        print(repr(t), "->", p._validate_domain(t))
    except Exception as e:
        print(repr(t), "-> RAISED", type(e).__name__, e)
