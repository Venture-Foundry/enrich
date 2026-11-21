"""Per-ISV signal packs: transparent, editable scoring on top of the other steps' output."""
import json, os, re
from .. import settings

CONFIG_PATHS = [os.path.join(settings.DATA_DIR, "config", "packs.json"), os.path.join(os.path.dirname(__file__), "..", "..", "config", "packs.json")]

def load():
    for p in CONFIG_PATHS:
        if os.path.exists(p):
            try:
                d = json.load(open(p, encoding="utf-8"))
                return {k: v for k, v in d.items() if not k.startswith("_") and isinstance(v, dict)}
            except ValueError:
                continue
    return {}

def columns(names):
    cols = []
    for n in names:
        cols += [f"enr_pack_{n}_score", f"enr_pack_{n}_hits"]
    return cols

def _truthy(v):
    return bool(v) and v not in ("no", "none", "No MX", "False")

def score(name, pack, res, text):
    pts, hits = 0, []
    t = text or ""
    for kw, w in (pack.get("keywords") or {}).items():
        n = len(re.findall(r"(?<![a-z])" + re.escape(kw.lower()) + r"(?![a-z])", t))
        if n:
            pts += min(n, 3) * w
            hits.append(f"kw:{kw}({min(n,3)})")
    stack = set((res.get("enr_tech_stack") or []) if isinstance(res.get("enr_tech_stack"), list) else str(res.get("enr_tech_stack", "")).split("; "))
    for tname, w in (pack.get("tech") or {}).items():
        if tname in stack:
            pts += w
            hits.append(f"tech:{tname}")
    for field, w in (pack.get("fields") or {}).items():
        if "~" in field:
            f, sub = field.split("~", 1)
            v = str(res.get(f, ""))
            ok = sub.lower() in v.lower()
        else:
            ok = _truthy(res.get(field))
        if ok:
            pts += w
            hits.append(f"field:{field}")
    return {f"enr_pack_{name}_score": pts, f"enr_pack_{name}_hits": hits}
