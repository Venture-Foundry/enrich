import os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["DATA_DIR"] = tempfile.mkdtemp()
from fastapi.testclient import TestClient
from app import main, cache

ok = fail = 0
def check(name, cond):
    global ok, fail
    if cond: ok += 1
    else: fail += 1; print("FAIL", name)

c = TestClient(main.app)
cache.put("acme.ae", "web_partner", {"_md": "## About\nWe are a Microsoft partner.", "enr_x": "1"})
r = c.get("/api/text/Acme.AE?focus=partner")
check("200", r.status_code == 200)
check("markdown returned", "Microsoft partner" in r.json()["markdown"])
check("chars", r.json()["chars"] == len(r.json()["markdown"]))
check("miss is 404", c.get("/api/text/nope.ae").status_code == 404)
check("bad focus is 400", c.get("/api/text/acme.ae?focus=zzz").status_code == 400)
print(f"{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
