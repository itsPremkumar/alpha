import re

_DOMAIN_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$")
print("domain .match CRLF:", bool(_DOMAIN_RE.match("a.example.com\r")))
print("domain .match LF:", bool(_DOMAIN_RE.match("a.example.com\n")))
print("domain .fullmatch CRLF:", bool(_DOMAIN_RE.fullmatch("a.example.com\r")))
print("domain .fullmatch LF:", bool(_DOMAIN_RE.fullmatch("a.example.com\n")))

# url_safety: is_blocked_address is_ip
import ipaddress
print("is_private CRLF not applicable")
# ComputerWorker .env pattern
PAT = re.compile(r"(?i)\.env(?:\.local)?$")
print("env CRLF:", bool(PAT.search("/.env.local\r")))
print("env LF:", bool(PAT.search("/.env.local\n")))
print("env clean:", bool(PAT.search("/.env.local")))
