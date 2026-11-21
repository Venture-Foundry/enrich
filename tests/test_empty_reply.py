import asyncio, os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["DATA_DIR"] = tempfile.mkdtemp(); os.environ["LLM_BASE_URL"] = "http://llm.test/v1"; os.environ["LLM_MODEL"] = "m"
import httpx
from app.steps import llm_step
ok = fail = 0
def check(n, c):
    global ok, fail
    if c: ok += 1
    else: fail += 1; print("FAIL", n)
def handler(req):  # what a reasoning model returns when thinking used the whole budget: content is null
    return httpx.Response(200, json={"choices": [{"message": {"content": None, "reasoning": "thinking..."}, "finish_reason": "length"}], "usage": {"prompt_tokens": 100, "completion_tokens": 3000}})
llm_step._transport = httpx.MockTransport(handler)
async def go():
    try:
        await llm_step.run("x.ae", "# Home\nstuff", "partner", {})
        return ""
    except RuntimeError as e:
        return str(e)
msg = asyncio.run(go())
check("null content gives a readable error, not a TypeError", "empty reply" in msg and "reasoning" in msg and "NoneType" not in msg)

def prose(req):
    return httpx.Response(200, json={"choices": [{"message": {"content": "The text provided only contains a login page, so I cannot profile this company."}}], "usage": {"prompt_tokens": 10, "completion_tokens": 5}})
llm_step._transport = httpx.MockTransport(prose)
async def go2():
    try:
        await llm_step.run("y.ae", "# Home\nlogin", "customer", {})
        return ""
    except RuntimeError as e:
        return str(e)
m2 = asyncio.run(go2())
check("prose reply is an error, not a silent blank row", "no JSON" in m2 and "login page" in m2)
print(f"{ok} passed, {fail} failed"); sys.exit(1 if fail else 0)
