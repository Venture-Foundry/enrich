"""ISV-fit signals: the LLM answers per-ISV yes/no questions from the website text (with evidence); scoring is done here, by rule."""
import hashlib, json, os
from .. import settings

CONFIG_PATHS = [os.path.join(settings.DATA_DIR, "config", "isvs.json"), os.path.join(os.path.dirname(__file__), "..", "..", "config", "isvs.json")]

def load():
    """Only ISVs that have at least one signal defined (stubs are ignored)."""
    for p in CONFIG_PATHS:
        if os.path.exists(p):
            try:
                d = json.load(open(p, encoding="utf-8"))
            except ValueError:
                continue
            return {k: v for k, v in d.items() if not k.startswith("_") and isinstance(v, dict) and v.get("signals")}
    return {}

def select(ids):
    defs = load()
    return {i: defs[i] for i in ids or [] if i in defs}

def digest(selected):
    return hashlib.sha1(json.dumps(selected, sort_keys=True).encode()).hexdigest()[:8]

def columns(ids):
    cols = []
    for i in ids:
        cols += [f"enr_isv_{i}_score", f"enr_isv_{i}_hits", f"enr_isv_{i}_incumbents", f"enr_isv_{i}_notes"]
    return cols

def prompt_block(selected):
    """Extra instructions appended to the customer prompt."""
    lines = []
    for i, d in selected.items():
        lines.append(f'ISV "{i}" - {d.get("label", i)}: {d.get("what_it_does", "")} Ideal customer: {d.get("ideal_customer", "")}')
        for s in d["signals"]:
            lines.append(f'  - signal "{s["id"]}": {s["question"]}')
        if d.get("competitors"):
            lines.append("  - competitors to look for by name: " + ", ".join(d["competitors"]))
    return ("\nisv_signals (object keyed by ISV id). For each ISV below return an object with: one key per signal id whose value is "
            '{"found": true or false, "evidence": "max 20 words taken from the text, or empty"}; '
            '"competitors_mentioned" (list, only names from that ISV\'s competitor list that appear in the text); '
            '"notes" (one short sentence on fit from the stated facts only, or ""). '
            '"found" is true ONLY if the text explicitly supports it; absence of mention is false, not a guess.\n' + "\n".join(lines))

def map_result(selected, raw):
    """Turn the model's isv_signals object into scored columns."""
    out, raw = {}, raw if isinstance(raw, dict) else {}
    for i, d in selected.items():
        r = raw.get(i) if isinstance(raw.get(i), dict) else {}
        pts, hits = 0, []
        maxpos = sum(s["weight"] for s in d["signals"] if s["weight"] > 0) or 1
        for s in d["signals"]:
            v = r.get(s["id"])
            if isinstance(v, dict) and v.get("found") is True:
                pts += s["weight"]
                ev = str(v.get("evidence") or "").strip()
                hits.append(f'{s["id"]}({s["weight"]:+d})' + (f': {ev}' if ev else ""))
        known = {str(c).lower() for c in d.get("competitors", [])}
        inc = [c for c in (r.get("competitors_mentioned") or []) if isinstance(c, str) and c.lower() in known]
        out[f"enr_isv_{i}_score"] = max(0, round(100 * pts / maxpos))
        out[f"enr_isv_{i}_hits"] = hits
        out[f"enr_isv_{i}_incumbents"] = inc
        out[f"enr_isv_{i}_notes"] = str(r.get("notes") or "")
    return out
