import asyncio, base64, hmac, json, os
import httpx
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response
from . import cache, jobs, settings
from .steps import llm_step
from .steps import isvs as isvs_mod, packs as packs_mod, web_step

app = FastAPI(title="Enrichment Portal")
STATIC = os.path.join(os.path.dirname(__file__), "static")

@app.middleware("http")
async def basic_auth(request: Request, call_next):
    if settings.PORTAL_USER and settings.PORTAL_PASSWORD:
        ok = False
        h = request.headers.get("authorization", "")
        if h.lower().startswith("basic "):
            try:
                u, _, p = base64.b64decode(h[6:]).decode().partition(":")
                ok = hmac.compare_digest(u, settings.PORTAL_USER) and hmac.compare_digest(p, settings.PORTAL_PASSWORD)
            except Exception:
                ok = False
        if not ok:
            return Response("Authentication required", 401, headers={"WWW-Authenticate": 'Basic realm="Enrichment Portal"'})
    return await call_next(request)

@app.on_event("startup")
async def startup():
    os.makedirs(os.path.join(settings.DATA_DIR, "jobs"), exist_ok=True)
    jobs.load_jobs()

@app.get("/", response_class=HTMLResponse)
async def index():
    return open(os.path.join(STATIC, "index.html"), encoding="utf-8").read()

@app.get("/api/config")
async def config():
    p = packs_mod.load()
    return {"crawl4ai_url": settings.CRAWL4AI_URL, "llm_enabled": settings.LLM_ENABLED, "llm_model": settings.LLM_MODEL if settings.LLM_ENABLED else "",
            "packs": {k: v.get("label", k) for k, v in p.items()}, "isvs": {k: v.get("label", k) for k, v in isvs_mod.load().items()}, "cache": cache.stats(), "cache_ttl_days": settings.CACHE_TTL_DAYS,
            "defaults": {"row_concurrency": settings.ROW_CONCURRENCY, "crawl_concurrency": settings.CRAWL_CONCURRENCY}}

@app.get("/api/llm-settings")
async def get_llm_settings():
    return settings.llm_public()

@app.put("/api/llm-settings")
async def put_llm_settings(request: Request):
    try:
        settings.save_llm(await request.json())
    except ValueError as e:
        raise HTTPException(400, str(e))
    return settings.llm_public()

@app.post("/api/llm-settings/test")
async def test_llm_settings(request: Request):
    try:
        body = await request.json()
    except ValueError:
        body = {}
    try:
        return await llm_step.ping((body.get("model") or "").strip() or None)
    except RuntimeError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=200)

@app.get("/api/health")
async def health(crawl: bool = False):
    """Ping crawl4ai; with ?crawl=true also run a real one-page crawl (needs the crawl4ai box to have internet)."""
    out = {"url": settings.CRAWL4AI_URL}
    async with httpx.AsyncClient(timeout=15) as c:
        try:
            r = await c.get(f"{settings.CRAWL4AI_URL}/health")
            out.update(reachable=True, http=r.status_code, body=r.text[:200])
        except httpx.HTTPError as e:
            return {**out, "reachable": False, "error": type(e).__name__}
    if crawl:
        try:
            res = await web_step.crawl(["https://example.com"])
            r0 = res[0] if res else {}
            out["test_crawl"] = {"success": r0.get("success"), "status": r0.get("status"), "html_chars": len(r0.get("html", "")), "error": r0.get("error", "")}
        except web_step.CrawlServerError as e:
            out["test_crawl"] = {"success": False, "error": str(e)}
    return out

@app.post("/api/preview")
async def preview(file: UploadFile = File(...)):
    raw = await file.read()
    cols, rows = jobs.parse_csv(raw)
    if not cols:
        raise HTTPException(400, "Could not read a header row from that file")
    dc = jobs.detect_domain_column(cols)
    withdom = sum(1 for r in rows if jobs.pick_domain(r, dc, cols))
    return {"columns": cols, "rows": len(rows), "domain_column": dc, "with_domain": withdom, "sample": rows[:5]}

@app.post("/api/jobs")
async def create(file: UploadFile = File(...), options: str = Form("{}")):
    try:
        opts = json.loads(options)
        job = jobs.create_job(file.filename or "upload.csv", await file.read(), opts)
    except (ValueError, KeyError) as e:
        raise HTTPException(400, str(e))
    asyncio.create_task(jobs.run_job(job))
    return job.summary()

@app.get("/api/jobs")
async def list_jobs():
    return sorted((j.summary() for j in jobs.JOBS.values()), key=lambda s: -(s["created"] or 0))

def _get(jid):
    j = jobs.JOBS.get(jid)
    if not j:
        raise HTTPException(404, "No such job")
    return j

@app.get("/api/jobs/{jid}")
async def job_status(jid: str):
    j = _get(jid)
    return {**j.summary(), "recent": list(j.recent), "options": j.options}

@app.post("/api/jobs/{jid}/cancel")
async def cancel(jid: str):
    j = _get(jid)
    j.cancel.set()
    return j.summary()

@app.post("/api/jobs/{jid}/rerun")
async def rerun(jid: str, force: bool = False):
    j = _get(jid)
    raw = open(os.path.join(j.dir, "input.csv"), "rb").read()
    opts = dict(j.options)
    opts["force"] = force
    new = jobs.create_job(j.name, raw, {**opts, "max_rows": j.options.get("max_rows", 0)})
    asyncio.create_task(jobs.run_job(new))
    return new.summary()

@app.get("/api/jobs/{jid}/download")
async def download(jid: str):
    j = _get(jid)
    base = os.path.splitext(j.name)[0]
    suffix = "" if j.status == "done" else "_partial"
    return Response(j.csv_bytes(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{base}_enriched{suffix}.csv"'})

@app.get("/api/text/{domain}")
async def site_text(domain: str, focus: str = ""):
    """The crawled site text kept in the cache for a domain (so other tools, e.g. Atlas, can re-read the source without re-crawling)."""
    if focus not in ("", "partner", "customer"):
        raise HTTPException(400, "focus must be blank, partner or customer")
    key = f"web_{focus}" if focus else "web"
    p = cache.get(domain.strip().lower(), key)
    if not p:
        raise HTTPException(404, f"No cached crawl for {domain} ({key})")
    md = p.get("_md") or ""
    return {"domain": domain.lower(), "focus": focus, "chars": len(md), "markdown": md}

@app.post("/api/cache/clear")
async def clear_cache():
    cache.clear()
    return {"ok": True}
