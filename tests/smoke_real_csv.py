import asyncio, csv, io, json, sys, time
sys.argv = ["x"]
from tests import run_test as rt
import httpx
from app import main

async def go():
    raw = open("/home/claude/apollo/uae_companies_201plus.csv", "rb").read()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://t") as c:
        await main.startup()
        t = time.time()
        pv = (await c.post("/api/preview", files={"file": ("uae.csv", raw)})).json()
        print("preview:", pv["rows"], "rows,", pv["with_domain"], "with domain, domain col =", pv["domain_column"], f"({time.time()-t:.1f}s)")
        t = time.time()
        opts = {"steps": ["dns", "tenant", "web", "packs"], "max_rows": 60, "packs": ["centrepal"], "row_concurrency": 8}
        j = (await c.post("/api/jobs", files={"file": ("uae.csv", raw)}, data={"options": json.dumps(opts)})).json()
        d = await rt.wait_done(c, j["id"])
        print("job:", d["status"], d["counts"], f"{time.time()-t:.1f}s")
        rows = rt.parse((await c.get(f"/api/jobs/{j['id']}/download")).text)
        print("output rows:", len(rows), "| columns:", len(rows[0]), "| first row name:", rows[0]["name"], "| enr_status:", rows[0]["enr_status"])
        ar = [r for r in rows if any("؀" <= ch <= "ۿ" for ch in r["name"])]
        print("arabic-named rows survived:", len(ar), (ar[0]["name"], ar[0]["enr_status"]) if ar else "")

if __name__ == "__main__":
    rt.start_mock()
    asyncio.run(go())
