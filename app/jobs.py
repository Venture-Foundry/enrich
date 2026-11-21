import asyncio, csv, io, json, os, time, uuid
from collections import deque
from datetime import datetime, timezone
from . import cache, settings
from .util import norm_domain, flat
from .steps import dns_step, tenant_step, web_step, llm_step, isvs as isvs_mod, packs as packs_mod
from .steps.web_step import CrawlServerError

DOMAIN_CANDIDATES = ["domain", "primary_domain", "website", "website_url", "url", "site", "web"]
META_COLS = ["enr_domain", "enr_status", "enr_errors", "enr_enriched_at"]
STEP_ORDER = ["dns", "tenant", "web", "packs", "llm"]
FOCUSES = ("", "partner", "customer", "both")

def llm_profiles(focus):
    """LLM profiles to run for a focus. No focus keeps the original short summary."""
    return {"partner": ["partner"], "customer": ["customer"], "both": ["partner", "customer"]}.get(focus, ["summary"])
JOBS: dict = {}

def detect_domain_column(columns):
    low = {c.lower().strip(): c for c in columns}
    for cand in DOMAIN_CANDIDATES:
        if cand in low:
            return low[cand]
    return None

def parse_csv(raw: bytes):
    text = raw.decode("utf-8-sig", errors="replace")
    rdr = csv.DictReader(io.StringIO(text))
    cols = rdr.fieldnames or []
    rows = [dict(r) for r in rdr]
    return cols, rows

def pick_domain(row, col, columns):
    for c in ([col] if col else []) + [x for x in columns if x.lower() in ("domain", "primary_domain", "website_url", "website", "url")]:
        if c and row.get(c):
            d = norm_domain(row[c])
            if d:
                return d
    return ""

def job_columns(options):
    steps = options["steps"]
    cols = list(META_COLS)
    if "dns" in steps: cols += dns_step.COLUMNS
    if "tenant" in steps: cols += tenant_step.COLUMNS
    if "web" in steps: cols += web_step.COLUMNS
    if "packs" in steps: cols += packs_mod.columns(options.get("packs", []))
    if "llm" in steps:
        for prof in llm_profiles(options.get("focus", "")):
            cols += llm_step.COLUMNS_BY_PROFILE[prof]
            if prof == "customer":
                cols += isvs_mod.columns(list(isvs_mod.select(options.get("isvs", []))))
        cols.append("enr_llm_model")
    return cols

class Job:
    def __init__(self, name, columns, rows, options, jid=None):
        self.id = jid or time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:5]
        self.name, self.in_cols, self.rows, self.options = name, columns, rows, options
        self.created = time.time()
        self.status = "queued"
        self.message = ""
        self.domains = [pick_domain(r, options.get("domain_column"), columns) for r in rows]
        self.results: dict = {}
        self.cancel = asyncio.Event()
        self.recent = deque(maxlen=25)
        self.counts = {"ok": 0, "partial": 0, "error": 0, "no_domain": 0}
        self.cache_hits = 0
        self.llm_used = 0
        self.llm_usage = {"in": 0, "out": 0}
        self.started = self.finished = None
        self.dir = os.path.join(settings.DATA_DIR, "jobs", self.id)
        self._locks: dict = {}

    @property
    def out_cols(self):
        return job_columns(self.options)

    def summary(self):
        done = len(self.results)
        return {"id": self.id, "name": self.name, "status": self.status, "message": self.message, "total": len(self.rows), "done": done,
                "counts": self.counts, "cache_hits": self.cache_hits, "created": self.created, "started": self.started, "finished": self.finished,
                "steps": self.options["steps"], "with_domain": sum(1 for d in self.domains if d), "llm_used": self.llm_used,
                "focus": self.options.get("focus", ""), "llm_model": self.options.get("llm_model") or (settings.LLM_MODEL if "llm" in self.options["steps"] else ""), "llm_tokens_in": self.llm_usage["in"], "llm_tokens_out": self.llm_usage["out"]}

    def save_meta(self):
        os.makedirs(self.dir, exist_ok=True)
        json.dump({"id": self.id, "name": self.name, "options": self.options, "status": self.status, "message": self.message, "created": self.created,
                   "started": self.started, "finished": self.finished, "in_cols": self.in_cols, "llm_usage": self.llm_usage, "llm_used": self.llm_used}, open(os.path.join(self.dir, "meta.json"), "w"))

    def csv_bytes(self):
        out_cols = self.out_cols
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(self.in_cols + out_cols)
        for i, row in enumerate(self.rows):
            res = self.results.get(i, {})
            w.writerow([row.get(c, "") for c in self.in_cols] + [flat(res.get(c, "")) for c in out_cols])
        return ("﻿" + buf.getvalue()).encode("utf-8")

def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

async def _cached(job, dom, step, fn):
    """Returns (payload, error). Cache hits are free; errors are never cached."""
    if not job.options.get("force"):
        hit = cache.get(dom, step)
        if hit is not None:
            job.cache_hits += 1
            return hit, None
    try:
        payload = await fn()
    except CrawlServerError:
        raise
    except Exception as e:
        return None, f"{step}: {str(e)[:140] or type(e).__name__}"
    cache.put(dom, step, payload)
    return payload, None

async def process_row(job: Job, i: int):
    row, dom = job.rows[i], job.domains[i]
    res = {"enr_domain": dom, "enr_enriched_at": _now()}
    steps = job.options["steps"]
    if not dom:
        res["enr_status"] = "no_domain"
        job.counts["no_domain"] += 1
        return res
    lock = job._locks.setdefault(dom, asyncio.Lock())
    errs, ok = [], 0
    async with lock:  # duplicate domains in one file are processed one after another (second is a cache hit)
        jobs = []
        if "dns" in steps: jobs.append(("dns", lambda: dns_step.run(dom)))
        if "tenant" in steps: jobs.append(("tenant", lambda: tenant_step.run(dom)))
        focus = job.options.get("focus", "")
        web_key = f"web_{focus}" if focus else "web"  # deeper focus crawls are cached separately from the basic crawl
        if focus == "partner": web_key += "_v2"  # v2 = deeper partner crawl (12 pages, sitemap); older partner crawls are not reused
        if "web" in steps: jobs.append((web_key, lambda: web_step.run(dom, int(job.options.get("extra_pages", 3)), focus=focus)))
        got = await asyncio.gather(*[_cached(job, dom, s, f) for s, f in jobs])
        web_payload = {}
        for (s, _), (payload, err) in zip(jobs, got):
            if err:
                errs.append(err)
                continue
            ok += 1
            if s == web_key:
                web_payload = payload
            if payload.get("_error"):
                errs.append(payload["_error"])
            res.update({k: v for k, v in payload.items() if not k.startswith("_")})
        if res.get("enr_web_status") == "blocked":
            errs.append("website blocked crawler (captcha/WAF)")
        if "packs" in steps:
            allp = packs_mod.load()
            for name in job.options.get("packs", []):
                if name in allp:
                    res.update(packs_mod.score(name, allp[name], res, web_payload.get("_text", "")))
        if "llm" in steps and llm_step is not None:
            elig = job.llm_used < int(job.options.get("llm_max_rows", 0))
            fc, fv = job.options.get("llm_filter_column"), job.options.get("llm_filter_values")
            if elig and fc and fv:
                allowed = {v.strip().lower() for v in fv.split(",") if v.strip()}
                elig = str(row.get(fc, "")).strip().lower() in allowed
            if elig and web_payload.get("_md"):
                job.llm_used += 1
                isv_defs = isvs_mod.select(job.options.get("isvs", []))
                for prof in llm_profiles(focus):
                    key = "llm" if prof == "summary" else f"llm_{prof}"
                    use_isv = isv_defs if prof == "customer" else None
                    if use_isv:
                        key += "_isv" + isvs_mod.digest(use_isv)  # new ISV definitions -> fresh answers, not a stale cache hit
                    model = job.options.get("llm_model") or settings.LLM_MODEL
                    key += "_" + llm_step.TAXONOMY_VERSION + "@" + model  # answers are cached per model, so comparing models on the same rows really calls each model
                    payload, err = await _cached(job, dom, key, lambda p=prof, v=use_isv, m=model: llm_step.run(dom, web_payload["_md"], p, job.llm_usage, v, m))
                    if err: errs.append(err)
                    else: res.update(payload)
    status = "ok" if not errs else ("partial" if ok else "error")
    res["enr_status"], res["enr_errors"] = status, " | ".join(errs)[:300]
    job.counts[status] += 1
    return res

def _recent_view(res):
    tech = res.get("enr_tech_stack") or []
    return {"domain": res.get("enr_domain", ""), "status": res.get("enr_status", ""), "mail": res.get("enr_mx_provider", ""),
            "m365": res.get("enr_m365_tenant", ""), "teams_sip": res.get("enr_teams_sip_dns", ""),
            "industry": res.get("enr_industry_guess", ""), "emirate": res.get("enr_hq_emirate_guess", ""),
            "tech": tech[:4] if isinstance(tech, list) else tech, "errors": res.get("enr_errors", "")}

async def run_job(job: Job):
    job.status, job.started = "running", time.time()
    job.save_meta()
    sem = asyncio.Semaphore(int(job.options.get("row_concurrency") or settings.ROW_CONCURRENCY))
    fails_in_row = {"n": 0}
    out = open(os.path.join(job.dir, "results.jsonl"), "w", encoding="utf-8")

    async def one(i):
        if job.cancel.is_set() or job.status == "error":
            return
        async with sem:
            if job.cancel.is_set() or job.status == "error":
                return
            try:
                res = await process_row(job, i)
                fails_in_row["n"] = 0
            except CrawlServerError as e:
                fails_in_row["n"] += 1
                if fails_in_row["n"] >= 5:
                    job.status, job.message = "error", f"Stopped: {e}"
                    return
                # one bad site must not leave a silently blank row: record it so it can be re-run
                res = {"enr_domain": job.domains[i], "enr_status": "error", "enr_errors": f"crawl server: {str(e)[:140]}", "enr_enriched_at": _now()}
                job.counts["error"] += 1
            except Exception as e:  # never let one row kill the job
                res = {"enr_domain": job.domains[i], "enr_status": "error", "enr_errors": f"internal: {type(e).__name__}: {str(e)[:120]}", "enr_enriched_at": _now()}
                job.counts["error"] += 1
            job.results[i] = res
            job.recent.appendleft(_recent_view(res))
            out.write(json.dumps({"i": i, "res": res}, ensure_ascii=False, default=str) + "\n")
            out.flush()

    try:
        await asyncio.gather(*[one(i) for i in range(len(job.rows))])
    finally:
        out.close()
    if job.status != "error":
        job.status = "cancelled" if job.cancel.is_set() else "done"
    job.finished = time.time()
    job.save_meta()
    open(os.path.join(job.dir, "enriched.csv"), "wb").write(job.csv_bytes())

def create_job(name, raw, options) -> Job:
    cols, rows = parse_csv(raw)
    if not cols:
        raise ValueError("CSV has no header row")
    n = int(options.get("max_rows") or 0)
    if n > 0:
        rows = rows[:n]
    options = dict(options)
    options["steps"] = [s for s in STEP_ORDER if s in options.get("steps", [])]
    options["llm_model"] = str(options.get("llm_model") or "").strip()  # "" = use the model saved in LLM settings
    options["focus"] = options.get("focus") or ""
    if options["focus"] not in FOCUSES:
        raise ValueError("focus must be partner, customer or both")
    options["isvs"] = [i for i in (options.get("isvs") or []) if isinstance(i, str)]
    if options["isvs"]:
        if options["focus"] not in ("customer", "both") or "llm" not in options["steps"]:
            raise ValueError("ISV fit signals need the LLM step with a customer or both focus")
        if not isvs_mod.select(options["isvs"]):
            raise ValueError("None of the selected ISVs has signals defined in config/isvs.json")
    if options["focus"] and "web" not in options["steps"]:
        raise ValueError("A partner/customer focus needs the website step (it controls how deeply each site is crawled)")
    if not options["steps"]:
        raise ValueError("Select at least one enrichment step")
    if "llm" in options["steps"] and not settings.LLM_ENABLED:
        raise ValueError("LLM step selected but LLM_BASE_URL / LLM_MODEL are not configured")
    if "llm" in options["steps"] and "web" not in options["steps"]:
        raise ValueError("The LLM step needs the website step (it summarises the crawled text)")
    job = Job(name, cols, rows, options)
    os.makedirs(job.dir, exist_ok=True)
    open(os.path.join(job.dir, "input.csv"), "wb").write(raw)
    JOBS[job.id] = job
    job.save_meta()
    return job

def load_jobs():
    root = os.path.join(settings.DATA_DIR, "jobs")
    if not os.path.isdir(root):
        return
    for jid in sorted(os.listdir(root)):
        d = os.path.join(root, jid)
        try:
            meta = json.load(open(os.path.join(d, "meta.json")))
            cols, rows = parse_csv(open(os.path.join(d, "input.csv"), "rb").read())
            n = int(meta["options"].get("max_rows") or 0)
            rows = rows[:n] if n > 0 else rows
            job = Job(meta["name"], cols, rows, meta["options"], jid=jid)
            job.created, job.started, job.finished = meta.get("created"), meta.get("started"), meta.get("finished")
            job.status = meta["status"] if meta["status"] in ("done", "cancelled", "error") else "interrupted"
            job.message = meta.get("message", "")
            job.llm_usage = meta.get("llm_usage") or {"in": 0, "out": 0}
            job.llm_used = int(meta.get("llm_used") or 0)
            rp = os.path.join(d, "results.jsonl")
            if os.path.exists(rp):
                for line in open(rp, encoding="utf-8"):
                    try:
                        o = json.loads(line)
                        job.results[o["i"]] = o["res"]
                        st = o["res"].get("enr_status")
                        if st in job.counts: job.counts[st] += 1
                    except (ValueError, KeyError):
                        continue
            JOBS[jid] = job
        except (OSError, ValueError, KeyError):
            continue
