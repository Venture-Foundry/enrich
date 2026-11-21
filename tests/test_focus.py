"""Partner / customer focus: page picking, LLM profiles (OpenAI-style and native Anthropic), usage counting, job validation.
Run: python -m tests.test_focus"""
import asyncio, json, os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["DATA_DIR"] = tempfile.mkdtemp()
os.environ["LLM_BASE_URL"] = "http://llm.test/v1"; os.environ["LLM_MODEL"] = "mock-model"
import httpx
from app import jobs, settings
from app.steps import isvs as isvs_mod, llm_step, web_step

OK = FAIL = 0
def check(name, cond, extra=""):
    global OK, FAIL
    if cond: OK += 1; print("  PASS", name)
    else: FAIL += 1; print("  FAIL", name, extra)

LINKS = {"internal": [{"href": h, "text": t} for h, t in (
    ("/contact", "Contact"), ("/about", "About"), ("/careers", "Careers"), ("/services", "Our services"), ("/partners", "Technology partners"),
    ("/industries", "Industries we serve"), ("/case-studies", "Case studies"), ("/locations", "Branches"), ("/news", "News"), ("/shop", "Shop"))], "external": []}

# --- page picking
basic, careers = web_step.pick_pages("https://x.ae", LINKS, 4)
check("basic crawl keeps contact/about/careers", any("/contact" in u for u in basic) and any("/about" in u for u in basic) and careers.endswith("/careers"), basic)
check("basic crawl ignores services/partners", not any("/services" in u or "/partners" in u for u in basic), basic)
p, _ = web_step.pick_pages("https://x.ae", LINKS, 8, "partner")
check("partner focus reads services, partners, industries, case studies", all(any(k in u for u in p) for k in ("/services", "/partners", "/industries", "/case-studies")), p)
check("partner focus still finds careers url", _.endswith("/careers"))
c, _ = web_step.pick_pages("https://x.ae", LINKS, 8, "customer")
check("customer focus reads locations and news", any("/locations" in u for u in c) and any("/news" in u for u in c), c)
tiny, _ = web_step.pick_pages("https://x.ae", LINKS, 2, "partner")
check("page cap respected", len(tiny) <= 2, tiny)

# --- LLM profiles, OpenAI-style
PARTNER_JSON = {"one_liner": "Integrates and manages IT for banks", "primary_focus": "Managed SOC", "summary": "IT integrator", "partner_type": "System integrator", "service_lines": ["Managed SOC", "ERP implementation"],
    "technologies": ["Azure", "SAP S/4HANA"], "vendor_partnerships": [{"vendor": "Microsoft", "status": "Solutions Partner", "evidence": "badge on partners page"}],
    "microsoft_azure_signals": ["Azure migration practice"], "own_products_ip": [], "industries_served": ["Banking"], "named_customers": ["Acme Bank"],
    "delivery_model": "24x7 managed", "geography": ["Dubai"], "certifications_awards": ["ISO 27001"], "scale_signals": "200 staff",
    "channel_signals": ["Resells Fortinet"], "key_people": [{"name": "A. Person", "title": "CEO"}], "confidence": "high"}
CUSTOMER_JSON = {"one_liner": "Sells fashion in 40 stores", "summary": "Retail group", "sector": "retail & e-commerce", "secondary_sectors": ["Real estate"], "sector_evidence": "40 stores, online shop", "sub_sector": "Fashion", "business_model": "B2C retail", "products_services": ["Apparel"],
    "customer_channels": ["WhatsApp", "Call centre"], "locations": ["Dubai", "40 stores"], "scale_signals": "40 stores", "technology_signals": ["Shopify"],
    "initiatives_news": ["New flagship"], "functions_signalled": ["IT"], "key_people": [], "confidence": "medium"}
ISV_JSON = {**CUSTOMER_JSON, "isv_signals": {
    "centrepal": {"runs_contact_centre": {"found": True, "evidence": "24/7 helpline"}, "high_volume_support": {"found": True, "evidence": ""}, "microsoft_stack": {"found": False},
                  "competitors_mentioned": ["Genesys", "NotInList"], "notes": "Runs a helpline"},
    "pulpoar": {"sells_tryon_categories": {"found": True, "evidence": "sells sunglasses"}, "already_has_tryon": {"found": True, "evidence": "AR try-on"}}}}
seen = []
def openai_handler(request):
    body = json.loads(request.content); prompt = body["messages"][0]["content"]; seen.append(body)
    j = PARTNER_JSON if "delivery / channel partner" in prompt else (ISV_JSON if "isv_signals" in prompt else CUSTOMER_JSON)
    return httpx.Response(200, json={"choices": [{"message": {"content": "```json\n" + json.dumps(j) + "\n```"}}], "usage": {"prompt_tokens": 7000, "completion_tokens": 900}})
llm_step._transport = httpx.MockTransport(openai_handler)

async def _partner_prompt(sel):
    await llm_step.run("x.ae", "stuff", "partner", {}, sel)
    return seen[-1]["messages"][0]["content"]

async def main():
    usage = {}
    r = await llm_step.run("x.ae", "# Home\n" + "word " * 20000, "partner", usage)
    check("partner columns all populated", set(r) == set(llm_step.PARTNER_COLUMNS), set(llm_step.PARTNER_COLUMNS) ^ set(r))
    check("vendor partnership flattened to text", r["enr_p_vendor_partnerships"] == ["Microsoft - Solutions Partner - badge on partners page"], r["enr_p_vendor_partnerships"])
    check("partner people formatted", r["enr_p_key_people"] == ["A. Person (CEO)"])
    check("usage counted", usage == {"in": 7000, "out": 900}, usage)
    check("text capped at LLM_MAX_CHARS", len(seen[-1]["messages"][0]["content"]) < settings.LLM_MAX_CHARS + 6000, len(seen[-1]["messages"][0]["content"]))
    check("prompt tells model web text is untrusted", "untrusted" in seen[-1]["messages"][0]["content"])
    rc = await llm_step.run("x.ae", "# Home\nstuff", "customer", usage)
    check("customer columns all populated", set(rc) == set(llm_step.CUSTOMER_COLUMNS))
    check("sector snapped to fixed list (case-insensitive)", rc["enr_c_sector"] == "Retail & e-commerce", rc["enr_c_sector"])
    check("partial sector name snapped to list", rc["enr_c_secondary_sectors"] == ["Real estate & property development"], rc["enr_c_secondary_sectors"])
    check("unknown sector kept visible as Other(...)", llm_step._sector("Space mining") == "Other (Space mining)")
    check("one-liner + sector evidence captured", rc["enr_c_one_liner"] and rc["enr_c_sector_evidence"])
    check("sector list is in the customer prompt", "Logistics, shipping & freight" in seen[-1]["messages"][0]["content"])
    check("usage accumulates", usage == {"in": 14000, "out": 1800}, usage)
    # --- ISV fit signals + OpenRouter pass-through
    sel = isvs_mod.select(["centrepal", "pulpoar", "skymod"])
    check("only ISVs with signals are selectable (stubs ignored)", set(sel) == {"centrepal", "pulpoar"}, set(sel))
    ri = await llm_step.run("x.ae", "# Home\nstuff", "customer", usage, sel)
    check("ISV block + signal questions in prompt", "runs_contact_centre" in seen[-1]["messages"][0]["content"] and "Genesys" in seen[-1]["messages"][0]["content"])
    check("ISV columns all present", all(c in ri for c in isvs_mod.columns(["centrepal", "pulpoar"])), set(isvs_mod.columns(["centrepal", "pulpoar"])) - set(ri))
    check("CentrePal score = weighted found signals (4+2 of 9)", ri["enr_isv_centrepal_score"] == 67, ri["enr_isv_centrepal_score"])
    check("evidence kept with hits", ri["enr_isv_centrepal_hits"][0].startswith("runs_contact_centre(+4): 24/7 helpline"), ri["enr_isv_centrepal_hits"])
    check("only listed competitors kept", ri["enr_isv_centrepal_incumbents"] == ["Genesys"], ri["enr_isv_centrepal_incumbents"])
    check("negative weight reduces PulpoAR score", ri["enr_isv_pulpoar_score"] in (12, 13) and any("already_has_tryon(-3)" in h for h in ri["enr_isv_pulpoar_hits"]), ri["enr_isv_pulpoar_score"])
    check("customer profile columns still there with ISVs", set(llm_step.CUSTOMER_COLUMNS) <= set(ri))
    check("ISVs ignored for partner profile", "isv_signals" not in (await _partner_prompt(sel)))
    settings.LLM_EXTRA_BODY = {"provider": {"data_collection": "deny"}, "reasoning": {"enabled": False}}
    await llm_step.run("x.ae", "x", "customer", usage)
    check("LLM_EXTRA_BODY merged into request", seen[-1].get("provider") == {"data_collection": "deny"} and seen[-1].get("reasoning") == {"enabled": False}, list(seen[-1]))
    settings.LLM_EXTRA_BODY = {}
    try:
        await llm_step.run("x.ae", "x", "bogus"); check("unknown profile rejected", False)
    except RuntimeError:
        check("unknown profile rejected", True)

    # --- native Anthropic provider
    settings.LLM_PROVIDER, settings.LLM_BASE_URL, settings.LLM_API_KEY = "anthropic", "https://api.anthropic.com", "k"
    calls = []
    def anth_handler(request):
        calls.append((str(request.url), dict(request.headers), json.loads(request.content)))
        return httpx.Response(200, json={"content": [{"type": "text", "text": json.dumps(PARTNER_JSON)}], "usage": {"input_tokens": 8000, "output_tokens": 1200}})
    llm_step._transport = httpx.MockTransport(anth_handler)
    u2 = {}
    r2 = await llm_step.run("x.ae", "# Home\nstuff", "partner", u2)
    check("anthropic endpoint + headers", calls[0][0].endswith("/v1/messages") and calls[0][1].get("x-api-key") == "k" and "anthropic-version" in calls[0][1], calls[0][:2])
    check("anthropic max_tokens set", calls[0][2]["max_tokens"] == settings.LLM_MAX_TOKENS)
    check("anthropic reply parsed + usage", r2["enr_p_type"] == "System integrator" and u2 == {"in": 8000, "out": 1200}, (r2.get("enr_p_type"), u2))

asyncio.run(main())

# --- job options
check("partner focus -> partner columns", jobs.job_columns({"steps": ["web", "llm"], "focus": "partner"})[-len(llm_step.PARTNER_COLUMNS):] == llm_step.PARTNER_COLUMNS)
both = jobs.job_columns({"steps": ["web", "llm"], "focus": "both"})
check("both focus -> partner + customer columns", all(c in both for c in llm_step.PARTNER_COLUMNS + llm_step.CUSTOMER_COLUMNS))
check("no focus -> original summary columns", jobs.job_columns({"steps": ["web", "llm"]})[-len(llm_step.SUMMARY_COLUMNS):] == llm_step.SUMMARY_COLUMNS)
check("LLM cache is keyed by model", "settings.LLM_MODEL" in open(os.path.join(os.path.dirname(__file__), "..", "app", "jobs.py")).read())
check("ISV columns added for customer focus", all(c in jobs.job_columns({"steps": ["web", "llm"], "focus": "customer", "isvs": ["centrepal"]}) for c in isvs_mod.columns(["centrepal"])))
check("ISV columns not added without customer focus", not any(c.startswith("enr_isv_") for c in jobs.job_columns({"steps": ["web", "llm"], "focus": "partner", "isvs": ["centrepal"]})))
csv = b"domain\nx.ae\n"
for opts, label in (({"steps": ["dns"], "focus": "partner"}, "focus without website step rejected"), ({"steps": ["web"], "focus": "nonsense"}, "unknown focus rejected"),
                    ({"steps": ["web", "llm"], "focus": "partner", "isvs": ["centrepal"]}, "ISVs without customer focus rejected"),
                    ({"steps": ["web", "llm"], "focus": "customer", "isvs": ["skymod"]}, "ISV stub with no signals rejected")):
    try:
        jobs.create_job("t.csv", csv, opts); check(label, False)
    except ValueError:
        check(label, True)
# --- settings screen API: saved in the portal (not .env), key never returned, safe URL changes
async def api_tests():
    from app import main
    llm_step._transport = httpx.MockTransport(openai_handler)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://t") as c:
        key = "sk-or-v1-abcdefgh1234567"
        r = await c.put("/api/llm-settings", json={"provider": "openai", "base_url": "https://openrouter.ai/api/v1/", "model": "vendor/model-one", "api_key": key,
                                                   "extra_body": '{"reasoning": {"enabled": false}}', "max_chars": 20000, "max_tokens": 2000})
        check("save settings from the portal", r.status_code == 200 and r.json()["model"] == "vendor/model-one" and r.json()["enabled"], r.text)
        check("API key never returned, only last 4", key not in r.text and r.json()["api_key_set"] and r.json()["api_key_hint"] == "...4567", r.text)
        check("settings applied live (no restart)", settings.LLM_MODEL == "vendor/model-one" and settings.LLM_API_KEY == key and settings.LLM_EXTRA_BODY == {"reasoning": {"enabled": False}} and settings.LLM_MAX_CHARS == 20000)
        check("trailing slash trimmed", settings.LLM_BASE_URL == "https://openrouter.ai/api/v1")
        f = settings._llm_file()
        check("saved to the data volume with 0600 permissions", os.path.exists(f) and (os.stat(f).st_mode & 0o777) == 0o600, oct(os.stat(f).st_mode))
        check("GET shows source = portal", (await c.get("/api/llm-settings")).json()["source"] == "portal")
        r = await c.put("/api/llm-settings", json={"model": "vendor/model-two"})
        check("blank key on later save keeps the saved key", r.json()["api_key_set"] and settings.LLM_API_KEY == key and settings.LLM_MODEL == "vendor/model-two")
        r = await c.put("/api/llm-settings", json={"extra_body": "{not json"})
        check("invalid extra JSON rejected", r.status_code == 400, r.text)
        r = await c.put("/api/llm-settings", json={"provider": "bogus"})
        check("unknown provider rejected", r.status_code == 400)
        r = await c.put("/api/llm-settings", json={"base_url": "ftp://x"})
        check("non-http base URL rejected", r.status_code == 400)
        r = await c.put("/api/llm-settings", json={"max_tokens": 5})
        check("out-of-range limit rejected", r.status_code == 400)
        t = (await c.post("/api/llm-settings/test", json={})).json()
        check("test call works and reports tokens", t["ok"] and t["tokens_in"] == 7000 and t["model"] == "vendor/model-two", t)
        t = (await c.post("/api/llm-settings/test", json={"model": "other/model"})).json()
        check("test can target another model", t["ok"] and seen[-1]["model"] == "other/model", seen[-1].get("model"))
        # explicit per-run model is used in the request and in the cache key
        await llm_step.run("x.ae", "# Home\nstuff", "customer", {}, None, "run/model")
        check("per-run model sent to the provider", seen[-1]["model"] == "run/model")
        # a failing provider gives a readable reason
        llm_step._transport = httpx.MockTransport(lambda req: httpx.Response(401, text="invalid api key"))
        t = (await c.post("/api/llm-settings/test", json={})).json()
        check("failed test shows HTTP status and reason", t["ok"] is False and "401" in t["error"] and "invalid api key" in t["error"], t)
        llm_step._transport = httpx.MockTransport(openai_handler)
        # security: pointing at a different endpoint drops the saved key
        r = await c.put("/api/llm-settings", json={"base_url": "https://evil.example/v1"})
        check("changing the URL clears the saved key", r.status_code == 200 and not r.json()["api_key_set"] and settings.LLM_API_KEY == "", r.text)
        r = await c.put("/api/llm-settings", json={"base_url": "https://openrouter.ai/api/v1", "api_key": key})
        check("key can be set together with a new URL", r.json()["api_key_set"])
        r = await c.put("/api/llm-settings", json={"clear_api_key": True})
        check("key can be cleared explicitly", not r.json()["api_key_set"])
        o = jobs.create_job("t.csv", b"domain\nx.ae\n", {"steps": ["web", "llm"], "focus": "customer", "llm_model": " run/model "}).options
        check("per-run model stored on the job", o["llm_model"] == "run/model", o)
asyncio.run(api_tests())

print(f"\n{OK} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
