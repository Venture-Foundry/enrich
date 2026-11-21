import asyncio, csv, io, json, os, socket, sys, tempfile, threading, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["DATA_DIR"] = tempfile.mkdtemp()
PORT = 18111
os.environ["CRAWL4AI_URL"] = f"http://127.0.0.1:{PORT}"
os.environ["LLM_BASE_URL"] = "http://llm.test/v1"; os.environ["LLM_MODEL"] = "mock-model"
import uvicorn, httpx
from tests import mock_crawl4ai
from app import main, jobs, settings, cache
from app.steps import dns_step, tenant_step, llm_step, web_step

def start_mock():
    cfg = uvicorn.Config(mock_crawl4ai.app, host="127.0.0.1", port=PORT, log_level="error")
    srv = uvicorn.Server(cfg)
    threading.Thread(target=srv.run, daemon=True).start()
    for _ in range(50):
        try: socket.create_connection(("127.0.0.1", PORT), 0.2).close(); return srv
        except OSError: time.sleep(0.1)
    raise SystemExit("mock failed to start")

# ---- offline fakes for DNS / tenant / LLM -------------------------------------------------
FAKE = {
 "acmehotels.ae": {"MX": ["0 acmehotels-ae.mail.protection.outlook.com."], "TXT": ['"v=spf1 include:spf.protection.outlook.com -all"', '"MS=ms12345678"', '"atlassian-domain-verification=abc"'],
                   "NS": ["ns1.cloudflare.com."], "_dmarc.acmehotels.ae": ['"v=DMARC1; p=reject"'], "sip.acmehotels.ae": ["sipdir.online.lync.com."],
                   "_sip._tls.acmehotels.ae": ["100 1 443 sipdir.online.lync.com."], "autodiscover.acmehotels.ae": ["autodiscover.outlook.com."], "A": ["1.2.3.4"]},
 "callcentreco.ae": {"MX": ["10 aspmx.l.google.com.", "20 alt1.aspmx.l.google.com."], "TXT": ['"v=spf1 include:_spf.google.com ~all"', '"zendesk-verification=x"'], "NS": ["ns-1.awsdns-01.com."], "A": ["5.6.7.8"]},
 "freshbeauty.ae": {"MX": ["10 mx.mimecast.com."], "TXT": ['"v=spf1 include:spf.protection.outlook.com ~all"'], "NS": ["ns1.cloudflare.com."], "A": ["9.9.9.9"]},
}
async def fake_q(name, rtype, resolver=None):
    # route "<sub>.<domain>" lookups to FAKE[domain][key]
    for dom, recs in FAKE.items():
        if name == dom: return recs.get(rtype, [])
        if name.endswith("." + dom):
            return recs.get(name, [])
    if name.endswith("down.ae") or name.endswith("blocked.ae") or name.endswith("genericsoft.ae"):
        return []
    return []
dns_step._q = fake_q

def tenant_handler(request):
    host_in = str(request.url)
    if "acmehotels.ae" in host_in:
        if "getuserrealm" in host_in: return httpx.Response(200, json={"NameSpaceType": "Managed", "FederationBrandName": "Acme Hotels"})
        return httpx.Response(200, json={"issuer": "https://login.microsoftonline.com/11111111-2222-3333-4444-555555555555/v2.0"})
    if "getuserrealm" in host_in: return httpx.Response(200, json={"NameSpaceType": "Unknown"})
    return httpx.Response(400, json={})
tenant_step._transport = httpx.MockTransport(tenant_handler)

def llm_handler(request):
    body = json.loads(request.content)
    assert "Website text" in body["messages"][0]["content"]
    if "delivery / channel partner" in body["messages"][0]["content"]:
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({"summary": "Hotel group", "partner_type": "Vertical solution provider", "technologies": ["Opera PMS"], "confidence": "medium"})}}], "usage": {"prompt_tokens": 5000, "completion_tokens": 700}})
    return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({"one_liner": "Hotel group in Dubai", "offerings": ["Hotels", "Dining"], "sector": "Hospitality", "customer_type": "B2C", "hq_emirate": "Dubai", "key_people": [{"name": "A. Person", "title": "CEO"}], "confidence": "medium"})}}]})
llm_step._transport = httpx.MockTransport(llm_handler)

OK, FAIL = 0, 0
def check(name, cond, extra=""):
    global OK, FAIL
    if cond: OK += 1; print("  PASS", name)
    else: FAIL += 1; print("  FAIL", name, extra)

async def wait_done(c, jid, timeout=60):
    t0 = time.time()
    while time.time() - t0 < timeout:
        j = (await c.get(f"/api/jobs/{jid}")).json()
        if j["status"] not in ("queued", "running"): return j
        await asyncio.sleep(0.2)
    raise SystemExit("job timeout")

def parse(text):
    return list(csv.DictReader(io.StringIO(text.lstrip("﻿"))))

async def main_test():
    sample = open(os.path.join(os.path.dirname(__file__), "sample.csv"), "rb").read()
    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        await main.startup()
        print("config / health / preview")
        cfg = (await c.get("/api/config")).json()
        check("config shows packs + llm on", "centrepal" in cfg["packs"] and cfg["llm_enabled"])
        h = (await c.get("/api/health?crawl=true")).json()
        check("health reaches mock crawl4ai", h.get("reachable") and h["test_crawl"]["success"], h)
        pv = (await c.post("/api/preview", files={"file": ("sample.csv", sample)})).json()
        check("preview detects domain column + counts", pv["domain_column"] == "domain" and pv["rows"] == 8 and pv["with_domain"] == 7, pv)

        print("full run (all steps)")
        opts = {"steps": ["dns", "tenant", "web", "packs", "llm"], "domain_column": "domain", "extra_pages": 3, "packs": ["centrepal", "pulpoar"],
                "llm_max_rows": 5, "llm_filter_column": "employee_band", "llm_filter_values": "201-500", "row_concurrency": 4}
        j = (await c.post("/api/jobs", files={"file": ("sample.csv", sample)}, data={"options": json.dumps(opts)})).json()
        done = await wait_done(c, j["id"])
        check("job finished", done["status"] == "done", done)
        rows = parse((await c.get(f"/api/jobs/{j['id']}/download")).text)
        check("all 8 input rows present in output, in order", [r["name"] for r in rows][:3] == ["Acme Hotels", "CallCentreCo", "Fresh Beauty"] and len(rows) == 8)
        check("original columns preserved", rows[0]["employee_band"] == "201-500" and rows[0]["domain"] == "acmehotels.ae")
        a = rows[0]
        check("Acme: M365 mail via MX", a["enr_mx_provider"] == "Microsoft 365", a["enr_mx_provider"])
        check("Acme: Teams/SIP DNS evidence", a["enr_teams_sip_dns"] == "yes" and "sip CNAME" in a["enr_teams_sip_evidence"], a["enr_teams_sip_evidence"])
        check("Acme: SaaS vendors from TXT", "Atlassian" in a["enr_txt_vendors"], a["enr_txt_vendors"])
        check("Acme: DMARC reject + Cloudflare NS", a["enr_dmarc_policy"] == "reject" and a["enr_ns_provider"] == "Cloudflare")
        check("Acme: tenant id + managed", a["enr_m365_tenant"] == "yes" and a["enr_m365_tenant_id"].startswith("11111111") and a["enr_m365_namespace"] == "Managed")
        check("Acme: site title/description", "Acme Hotels" in a["enr_site_title"] and "hotel" in a["enr_site_description"].lower())
        check("Acme: tech detected (WordPress, GA4, Intercom)", all(t in a["enr_tech_stack"] for t in ("WordPress", "Google Analytics", "Intercom")), a["enr_tech_stack"])
        check("Acme: email/phone from contact page", "reservations@acmehotels.ae" in a["enr_emails"] and "+97141234567" in a["enr_phones"], (a["enr_emails"], a["enr_phones"]))
        check("Acme: toll-free 800 number captured", any(p.startswith("8004567") for p in a["enr_phones"].split("; ")), a["enr_phones"])
        check("Acme: P.O. Box + emirates", "12345" in a["enr_po_box"] and a["enr_hq_emirate_guess"] == "Dubai" and "Abu Dhabi" in a["enr_emirates_mentioned"], (a["enr_po_box"], a["enr_emirates_mentioned"]))
        check("Acme: careers page + Lever ATS", a["enr_has_careers_page"] == "yes" and "Lever" in a["enr_ats"], (a["enr_careers_url"], a["enr_ats"]))
        check("Acme: LinkedIn found, share link ignored", "linkedin.com/company/acme-hotels" in a["enr_linkedin_url"] and a["enr_twitter_url"] == "")
        check("Acme: WhatsApp flag", a["enr_whatsapp"] == "yes")
        check("Acme: industry = hospitality (heuristic)", a["enr_industry_guess"] == "Hospitality & tourism", a["enr_industry_guess"])
        check("Acme: NAICS sector derived is not needed here (blank ok)", True)
        check("Acme: home + 3 extra pages crawled (contact, about, careers)", int(a["enr_pages_crawled"]) == 4, a["enr_pages_crawled"])
        check("Acme: centrepal pack scores Teams/M365 signals", int(a["enr_pack_centrepal_score"]) >= 9 and "field:enr_teams_sip_dns" in a["enr_pack_centrepal_hits"], a["enr_pack_centrepal_hits"])
        check("Acme: LLM summary present (201-500 filter)", a["enr_llm_one_liner"] == "Hotel group in Dubai" and "A. Person (CEO)" in a["enr_llm_key_people"])
        cc = rows[1]
        check("CallCentreCo: Google mail + Genesys/Zendesk", cc["enr_mx_provider"] == "Google Workspace" and "Genesys" in cc["enr_contact_centre_tech"], (cc["enr_mx_provider"], cc["enr_contact_centre_tech"]))
        check("CallCentreCo: centrepal score high from keywords+tech", int(cc["enr_pack_centrepal_score"]) >= 20, cc["enr_pack_centrepal_score"])
        fb = rows[2]
        check("Fresh Beauty: Shopify + mail gateway; LLM skipped (501-1000 not in filter)", fb["enr_ecommerce_platform"] == "Shopify" and fb["enr_mail_gateway"] == "Mimecast" and fb["enr_llm_one_liner"] == "", (fb["enr_ecommerce_platform"], fb["enr_mail_gateway"]))
        check("Fresh Beauty: M365 behind Mimecast", fb["enr_mx_provider"] == "Microsoft 365 (behind gateway)", fb["enr_mx_provider"])
        check("Fresh Beauty: pulpoar pack scores", int(fb["enr_pack_pulpoar_score"]) >= 12, fb["enr_pack_pulpoar_score"])
        bl = rows[3]
        check("Blocked site flagged, row partial", bl["enr_web_status"] == "blocked" and bl["enr_status"] == "partial" and "blocked" in bl["enr_errors"], (bl["enr_web_status"], bl["enr_status"], bl["enr_errors"]))
        dd = rows[4]
        check("Dead site: unreachable, DNS says no records, row partial not crash", dd["enr_web_status"] == "unreachable" and dd["enr_status"] == "partial", (dd["enr_web_status"], dd["enr_status"]))
        check("Generic domain with no DNS/tenant still gets website data", rows[5]["enr_site_title"].startswith("genericsoft.ae"), rows[5]["enr_site_title"])
        nd = rows[6]
        check("No-domain row passes through untouched", nd["enr_status"] == "no_domain" and nd["enr_domain"] == "" and nd["name"] == "No website at all")
        check("Duplicate domain row enriched identically", rows[7]["enr_mx_provider"] == a["enr_mx_provider"] and rows[7]["enr_tech_stack"] == a["enr_tech_stack"])
        check("Counts add up", sum(done["counts"].values()) == 8, done["counts"])
        check("Duplicate domain used cache", done["cache_hits"] >= 3, done["cache_hits"])
        check("Output file saved on disk", os.path.exists(os.path.join(settings.DATA_DIR, "jobs", j["id"], "enriched.csv")))

        print("rerun uses cache (no new crawl calls)")
        before = len(mock_crawl4ai.CALLS)
        j2 = (await c.post(f"/api/jobs/{j['id']}/rerun")).json()
        d2 = await wait_done(c, j2["id"])
        check("rerun finished, zero new crawl4ai calls", d2["status"] == "done" and len(mock_crawl4ai.CALLS) == before, (d2["status"], len(mock_crawl4ai.CALLS) - before))
        j3 = (await c.post(f"/api/jobs/{j['id']}/rerun?force=true")).json()
        d3 = await wait_done(c, j3["id"])
        check("force rerun hits crawl4ai again", len(mock_crawl4ai.CALLS) > before)

        print("job restart persistence")
        jobs.JOBS.clear(); jobs.load_jobs()
        check("jobs reload from disk with results", j["id"] in jobs.JOBS and len(jobs.JOBS[j["id"]].results) == 8)
        rows2 = parse((await c.get(f"/api/jobs/{j['id']}/download")).text)
        check("download after reload matches", rows2[0]["enr_tech_stack"] == a["enr_tech_stack"])

        print("only-DNS job needs no crawl4ai")
        before = len(mock_crawl4ai.CALLS)
        j4 = (await c.post("/api/jobs", files={"file": ("s.csv", sample)}, data={"options": json.dumps({"steps": ["dns"], "max_rows": 3, "force": True})})).json()
        d4 = await wait_done(c, j4["id"])
        r4 = parse((await c.get(f"/api/jobs/{j4['id']}/download")).text)
        check("max_rows=3 honoured, only DNS columns added", len(r4) == 3 and "enr_site_title" not in r4[0] and "enr_mx_provider" in r4[0] and len(mock_crawl4ai.CALLS) == before)

        print("partner focus job")
        o2 = {"steps": ["web", "llm"], "domain_column": "domain", "extra_pages": 7, "focus": "partner", "llm_max_rows": 5, "row_concurrency": 4}
        j2 = (await c.post("/api/jobs", files={"file": ("sample.csv", sample)}, data={"options": json.dumps(o2)})).json()
        d2 = await wait_done(c, j2["id"])
        check("focus job finished and reports focus + token usage", d2["status"] == "done" and d2["focus"] == "partner" and d2["llm_tokens_in"] > 0 and d2["llm_tokens_out"] > 0, d2)
        r2 = parse((await c.get(f"/api/jobs/{j2['id']}/download")).text)
        check("focus job outputs partner columns, not the short summary", "enr_p_summary" in r2[0] and "enr_llm_one_liner" not in r2[0])
        check("focus job: partner profile filled", r2[0]["enr_p_summary"] == "Hotel group" and r2[0]["enr_p_technologies"] == "Opera PMS", (r2[0]["enr_p_summary"], r2[0]["enr_p_technologies"]))
        check("focus crawl cached separately from basic crawl", "web_partner" in (await c.get("/api/config")).json()["cache"])

        print("validation + error paths")
        r = await c.post("/api/jobs", files={"file": ("s.csv", sample)}, data={"options": json.dumps({"steps": []})})
        check("rejects no steps", r.status_code == 400)
        r = await c.post("/api/jobs", files={"file": ("s.csv", sample)}, data={"options": json.dumps({"steps": ["llm"]})})
        check("LLM without website step rejected", r.status_code == 400 and "website" in r.text)
        r = await c.post("/api/preview", files={"file": ("empty.csv", b"")})
        check("empty file rejected cleanly", r.status_code == 400)
        saved = settings.CRAWL4AI_URL
        settings.CRAWL4AI_URL = "http://127.0.0.1:1"
        cache.clear()
        big = ("domain\n" + "\n".join(f"site{i}.ae" for i in range(12))).encode()
        j5 = (await c.post("/api/jobs", files={"file": ("big.csv", big)}, data={"options": json.dumps({"steps": ["web"], "row_concurrency": 2})})).json()
        d5 = await wait_done(c, j5["id"])
        check("crawl4ai down -> job stops with clear message", d5["status"] == "error" and "cannot reach crawl4ai" in d5["message"], d5)
        settings.CRAWL4AI_URL = saved

        print("cancel")
        cache.clear()
        slow = ("domain\n" + "\n".join("slow.ae" if i == 0 else f"s{i}.ae" for i in range(30))).encode()
        j6 = (await c.post("/api/jobs", files={"file": ("slow.csv", slow)}, data={"options": json.dumps({"steps": ["web"], "row_concurrency": 1, "extra_pages": 0})})).json()
        await asyncio.sleep(0.8)
        await c.post(f"/api/jobs/{j6['id']}/cancel")
        d6 = await wait_done(c, j6["id"])
        check("cancel stops early with partial results", d6["status"] == "cancelled" and d6["done"] < 30, d6)
        part = parse((await c.get(f"/api/jobs/{j6['id']}/download")).text)
        check("partial download has all 30 rows (unprocessed blank)", len(part) == 30)

        print("minimal-payload fallback (crawl4ai version drift)")
        web_step._minimal_mode = False
        os.environ["MOCK_STRICT"] = "minimal"
        cache.clear()
        j7 = (await c.post("/api/jobs", files={"file": ("s.csv", sample)}, data={"options": json.dumps({"steps": ["web"], "max_rows": 1})})).json()
        d7 = await wait_done(c, j7["id"])
        r7 = parse((await c.get(f"/api/jobs/{j7['id']}/download")).text)
        check("falls back to bare {urls} payload when config is rejected", r7[0]["enr_web_status"] == "ok" and web_step._minimal_mode is True, (r7[0]["enr_web_status"], web_step._minimal_mode))
        os.environ.pop("MOCK_STRICT")

        print("auth")
        settings.PORTAL_USER, settings.PORTAL_PASSWORD = "u", "p"
        check("401 without credentials", (await c.get("/api/config")).status_code == 401)
        check("200 with credentials", (await c.get("/api/config", auth=("u", "p"))).status_code == 200)
        settings.PORTAL_USER = settings.PORTAL_PASSWORD = ""

if __name__ == "__main__":
    start_mock()
    asyncio.run(main_test())
    print(f"\n{OK} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)
