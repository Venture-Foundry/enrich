"""Partner / customer focus: page picking, LLM profiles (OpenAI-style and native Anthropic), usage counting, job validation.
Run: python -m tests.test_focus"""
import asyncio, json, os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["DATA_DIR"] = tempfile.mkdtemp()
os.environ["LLM_BASE_URL"] = "http://llm.test/v1"; os.environ["LLM_MODEL"] = "mock-model"
import httpx
from app import jobs, settings
from app.steps import llm_step, web_step

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
seen = []
def openai_handler(request):
    body = json.loads(request.content); prompt = body["messages"][0]["content"]; seen.append(body)
    j = PARTNER_JSON if "delivery / channel partner" in prompt else CUSTOMER_JSON
    return httpx.Response(200, json={"choices": [{"message": {"content": "```json\n" + json.dumps(j) + "\n```"}}], "usage": {"prompt_tokens": 7000, "completion_tokens": 900}})
llm_step._transport = httpx.MockTransport(openai_handler)

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
csv = b"domain\nx.ae\n"
for opts, label in (({"steps": ["dns"], "focus": "partner"}, "focus without website step rejected"), ({"steps": ["web"], "focus": "nonsense"}, "unknown focus rejected")):
    try:
        jobs.create_job("t.csv", csv, opts); check(label, False)
    except ValueError:
        check(label, True)
print(f"\n{OK} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
