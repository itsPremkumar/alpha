import sys
sys.path.insert(0, "backend/packages/harness/alpha")
from egress import policy

cases = [
    "example.com",
    "evil.com\n",
    "evil.com\r\n",
    "a-b.example.com",
    "example.com\n\n",
    "EXAMPLE.COM",
    "bad domain",
    "example..com",
    "123.45.67.89",
    "localhost",
    "xn--",
]
for t in cases:
    try:
        print(repr(t), "->", policy._validate_domain(t))
    except Exception as e:
        print(repr(t), "-> RAISED", type(e).__name__, e)
