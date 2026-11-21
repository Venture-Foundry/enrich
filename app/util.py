import re
from urllib.parse import urlparse

def norm_domain(value: str) -> str:
    """Return a bare lowercase hostname from a domain/URL-ish string, or ''."""
    v = (value or "").strip().lower()
    if not v:
        return ""
    if "://" not in v:
        v = "http://" + v
    try:
        host = urlparse(v).hostname or ""
    except ValueError:
        return ""
    host = host.strip(".")
    if host.startswith("www."):
        host = host[4:]
    if not host or "." not in host or " " in host:
        return ""
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        return ""
    if not re.fullmatch(r"[a-z0-9.-]+", host):
        return ""
    return host

def flat(v):
    """Flatten values for CSV cells."""
    if v is None:
        return ""
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, (list, tuple, set)):
        return "; ".join(str(x) for x in v)
    if isinstance(v, dict):
        return "; ".join(f"{k}={x}" for k, x in v.items())
    return v
