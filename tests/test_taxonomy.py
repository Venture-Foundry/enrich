import asyncio, os, sys, tempfile, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["DATA_DIR"] = tempfile.mkdtemp(); os.environ["LLM_BASE_URL"] = "http://llm.test/v1"; os.environ["LLM_MODEL"] = "m"
import httpx
from app.steps import llm_step
ok = fail = 0
def check(n, c):
    global ok, fail
    if c: ok += 1
    else: fail += 1; print("FAIL", n)
seen = {}
def handler(req):
    seen["prompt"] = json.loads(req.content)["messages"][-1]["content"]
    return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({"one_liner": "x", "sector": "Staffing & HR services", "secondary_sectors": ["Software & SaaS"], "confidence": "high"})}}], "usage": {"prompt_tokens": 10, "completion_tokens": 5}})
llm_step._transport = httpx.MockTransport(handler)
r = asyncio.run(llm_step.run("x.ae", "# Home\nstuff", "customer", {}))
p = seen["prompt"]
check("every sector has a definition", all(t in llm_step.SECTOR_DEFS and llm_step.SECTOR_DEFS[t] for t in llm_step.TAXONOMY) and len(llm_step.TAXONOMY) == 33)
check("prompt carries definitions and rules", "recruitment agencies, executive search" in p and "Sector rules:" in p and "job board" in p)
check("sector parsed", r["enr_c_sector"] == "Staffing & HR services" and r["enr_c_secondary_sectors"] == ["Software & SaaS"])
check("off-list value is flagged, not silently kept", llm_step._sector("Recruitment") .startswith("Other"))
check("taxonomy version exists for cache key", llm_step.TAXONOMY_VERSION)
print(f"{ok} passed, {fail} failed"); sys.exit(1 if fail else 0)
