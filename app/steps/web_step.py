"""Website enrichment through crawl4ai's /crawl endpoint + rule-based extraction (no LLM)."""
import asyncio, json, re
from urllib.parse import urljoin, urlparse
import httpx
from bs4 import BeautifulSoup
from .. import settings
from . import tech, industry

COLUMNS = [
    "enr_web_status", "enr_web_final_url", "enr_site_title", "enr_site_description", "enr_site_language", "enr_arabic_site",
    "enr_site_cms", "enr_ecommerce_platform", "enr_tech_stack", "enr_chat_support_tools", "enr_crm_marketing_tools",
    "enr_contact_centre_tech", "enr_analytics_ads", "enr_hosting_cdn", "enr_payments", "enr_ats",
    "enr_has_careers_page", "enr_careers_url", "enr_emails", "enr_phones", "enr_whatsapp", "enr_po_box",
    "enr_linkedin_url", "enr_twitter_url", "enr_facebook_url", "enr_instagram_url", "enr_youtube_url",
    "enr_emirates_mentioned", "enr_hq_emirate_guess", "enr_industry_naics", "enr_industry_guess",
    "enr_industry_runner_up", "enr_industry_confidence", "enr_pages_crawled", "enr_text_chars",
]

_sem = None
_minimal_mode = False  # flips to True if the server rejects our config objects (version drift)

def _semaphore():
    global _sem
    if _sem is None:
        _sem = asyncio.Semaphore(settings.CRAWL_CONCURRENCY)
    return _sem

class CrawlServerError(Exception):
    """crawl4ai unreachable / misbehaving (not a per-site problem)."""

def _headers():
    h = {"Content-Type": "application/json"}
    if settings.CRAWL4AI_TOKEN:
        h["Authorization"] = f"Bearer {settings.CRAWL4AI_TOKEN}"
    return h

def _payload(urls, minimal=False):
    if minimal:
        return {"urls": urls}
    browser = json.loads(settings.CRAWL4AI_BROWSER_CONFIG) if settings.CRAWL4AI_BROWSER_CONFIG else \
        {"type": "BrowserConfig", "params": {"headless": True}}
    crawler = json.loads(settings.CRAWL4AI_CRAWLER_CONFIG) if settings.CRAWL4AI_CRAWLER_CONFIG else \
        {"type": "CrawlerRunConfig", "params": {"cache_mode": "bypass", "page_timeout": settings.CRAWL_PAGE_TIMEOUT_MS}}
    return {"urls": urls, "browser_config": browser, "crawler_config": crawler}

def _normalize(r: dict) -> dict:
    md = r.get("markdown")
    if isinstance(md, dict):
        md = md.get("raw_markdown") or md.get("markdown_with_citations") or ""
    return {
        "url": r.get("url", ""), "final_url": r.get("redirected_url") or r.get("url", ""),
        "success": bool(r.get("success")), "status": r.get("status_code"),
        "html": r.get("html") or r.get("cleaned_html") or "", "markdown": md or "",
        "links": r.get("links") or {}, "error": r.get("error_message") or "",
    }

async def crawl(urls, client=None) -> list:
    """POST to crawl4ai; returns normalized result dicts in request order (best effort)."""
    global _minimal_mode
    own = client is None
    client = client or httpx.AsyncClient(timeout=settings.CRAWL_HTTP_TIMEOUT_S)
    try:
        async with _semaphore():
            for attempt in (0, 1):
                try:
                    r = await client.post(f"{settings.CRAWL4AI_URL}/crawl", json=_payload(urls, _minimal_mode), headers=_headers())
                except (httpx.ConnectError, httpx.ConnectTimeout) as e:
                    raise CrawlServerError(f"cannot reach crawl4ai at {settings.CRAWL4AI_URL}: {type(e).__name__}")
                except httpx.HTTPError as e:
                    raise CrawlServerError(f"crawl4ai request failed: {type(e).__name__}")
                if r.status_code in (400, 422) and not _minimal_mode and attempt == 0:
                    _minimal_mode = True  # retry once with the bare {"urls": [...]} payload
                    continue
                break
        if r.status_code in (401, 403):
            raise CrawlServerError("crawl4ai rejected the request (auth) - set CRAWL4AI_TOKEN")
        if r.status_code >= 500:
            raise CrawlServerError(f"crawl4ai server error {r.status_code}")
        if r.status_code >= 400:
            raise CrawlServerError(f"crawl4ai returned {r.status_code}: {r.text[:200]}")
        data = r.json()
        raw = data.get("results")
        if raw is None and "result" in data:
            raw = [data["result"]]
        return [_normalize(x) for x in (raw or [])]
    finally:
        if own:
            await client.aclose()

BLOCK_MARKERS = ("just a moment", "attention required", "access denied", "enable javascript and cookies", "verify you are human",
                 "captcha", "request blocked", "403 forbidden", "are you a robot")

def _looks_blocked(html, text):
    if len(text) > 1500:
        return False
    low = (html[:6000] + " " + text).lower()
    return any(m in low for m in BLOCK_MARKERS)

LINK_CATS = [
    ("contact", re.compile(r"contact|reach-us|get-in-touch|enquir|اتصل|تواصل", re.I)),
    ("about", re.compile(r"about|who-we-are|our-story|company-profile|من-نحن", re.I)),
    ("careers", re.compile(r"career|jobs|vacanc|join-us|work-with-us|recruit|opportunit|وظائف", re.I)),
    ("team", re.compile(r"leadership|management|our-team|board|people|meet-the", re.I)),
]

# Extra pages worth reading when profiling a company in depth (focus = partner / customer / both).
PARTNER_CATS = [
    ("services", re.compile(r"service|what-we-do|capabilit|offering|expertise|managed|consult|خدمات", re.I)),
    ("solutions", re.compile(r"solution|platform|technolog|practice|accelerator|حلول", re.I)),
    ("partners", re.compile(r"partner|alliance|vendor|ecosystem|\baws\b|google-cloud|salesforce|\bsap\b|oracle|cisco|\bdell\b|\bhp\b", re.I)),
    ("microsoft", re.compile(r"microsoft|azure|dynamics|power-?platform|m365|office-?365|copilot|\bcsp\b", re.I)),
    ("industries", re.compile(r"industr|sector|verticals?|who-we-serve|we-serve|قطاعات", re.I)),
    ("clients", re.compile(r"client|customer|case-stud|success|portfolio|reference|projects?|testimonial|عملاء", re.I)),
    ("certifications", re.compile(r"certif|award|accredit|compliance|iso-?27|iso-?9", re.I)),
]
CUSTOMER_CATS = [
    ("products", re.compile(r"product|service|what-we-do|brands?|menu|catalog|shop|offering|منتجات|خدمات", re.I)),
    ("locations", re.compile(r"location|branches|stores?|find-us|outlets|network|offices|presence|فروع", re.I)),
    ("news", re.compile(r"news|press|media|updates|announcement|blog|investor|csr|sustainab|أخبار", re.I)),
    ("scale", re.compile(r"our-story|history|milestone|facts|at-a-glance|vision|strategy|group|subsidiar", re.I)),
]
ALL_CATS = LINK_CATS + PARTNER_CATS + CUSTOMER_CATS
FOCUS_CATS = {"partner": PARTNER_CATS, "customer": CUSTOMER_CATS, "both": PARTNER_CATS + CUSTOMER_CATS}
MAX_EXTRA_PAGES = 4        # default (no focus) cap
MAX_EXTRA_PAGES_FOCUS = 8  # cap when a customer/both focus is selected
MAX_EXTRA_PAGES_PARTNER = 12  # partner capability needs more pages (services, vendors, case studies)
MIN_EXTRA_PAGES_PARTNER = 10  # floor, so the form's page setting cannot starve a partner profile
PER_CAT_PARTNER = 2           # up to two pages per category for partners (several service or solution pages)

def _same_site(host_a, host_b):
    strip = lambda h: (h or "").lower().removeprefix("www.")
    return strip(host_a) == strip(host_b)

def pick_pages(home_url, links, extra, focus="", more=None):
    """Choose up to `extra` internal pages and report the careers link.
    Without a focus: contact, about, careers, team. With focus partner/customer/both the focus categories come first
    (services, partners, industries, clients... / products, locations, news...) followed by the basics.
    Partner focus reads up to PER_CAT_PARTNER pages per category. `more` = extra candidate URLs (e.g. from the sitemap)."""
    base_host = urlparse(home_url).hostname
    cands = []
    internal = (links or {}).get("internal") or []
    external = (links or {}).get("external") or []
    for item in list(internal) + list(external):
        href = (item.get("href") if isinstance(item, dict) else str(item)) or ""
        text = (item.get("text") if isinstance(item, dict) else "") or ""
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        absu = urljoin(home_url, href)
        if not _same_site(urlparse(absu).hostname, base_host):
            continue
        cands.append((absu.split("#")[0], f"{urlparse(absu).path} {text}"))
    seen = {u for u, _ in cands}
    for href in more or []:
        absu = urljoin(home_url, href).split("#")[0]
        if absu not in seen and _same_site(urlparse(absu).hostname, base_host):
            seen.add(absu)
            cands.append((absu, urlparse(absu).path))
    cats = FOCUS_CATS.get(focus, []) + LINK_CATS
    per_cat = PER_CAT_PARTNER if focus == "partner" else 1
    chosen, careers_url = [], ""
    for name, rx in cats:
        took = 0
        for u, label in cands:
            if not rx.search(label) or u.rstrip("/") == home_url.rstrip("/"):
                continue
            if name == "careers" and not careers_url:
                careers_url = u
            if u in [c[1] for c in chosen]:
                continue
            if len(chosen) >= extra:
                break
            chosen.append((name, u))
            took += 1
            if took >= per_cat or name in ("contact", "careers", "team", "about"):
                break
    return [u for _, u in chosen], careers_url

_LOC_RX = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>", re.I)
_SKIP_URL = re.compile(r"\.(pdf|jpe?g|png|gif|webp|svg|zip|xml)(\?|$)|/(tag|category|author|wp-json|feed)/|/(ar|fr|de|ru)/", re.I)

def parse_sitemap(xml, limit=400):
    """Return (page_urls, child_sitemaps) from a sitemap or sitemap index body."""
    locs = _LOC_RX.findall(xml or "")
    kids = [u for u in locs if u.lower().split("?")[0].endswith(".xml")]
    pages = [u for u in locs if u not in kids and not _SKIP_URL.search(u)]
    return pages[:limit], kids

async def sitemap_urls(final_url, client=None, limit=400):
    """Best-effort sitemap read for sites whose menus are JavaScript only. Never raises."""
    origin = f"{urlparse(final_url).scheme}://{urlparse(final_url).netloc}"
    own = client is None
    client = client or httpx.AsyncClient(timeout=8, follow_redirects=True)
    try:
        r = await client.get(origin + "/sitemap.xml", timeout=8, follow_redirects=True)
        if r.status_code >= 400:
            return []
        pages, kids = parse_sitemap(r.text, limit)
        for k in kids[:3]:
            if len(pages) >= limit:
                break
            try:
                rk = await client.get(k, timeout=8, follow_redirects=True)
                if rk.status_code < 400:
                    pages += parse_sitemap(rk.text, limit)[0]
            except Exception:
                continue
        return pages[:limit]
    except Exception:
        return []
    finally:
        if own:
            await client.aclose()

EMAIL_RX = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}")
JUNK_EMAIL = re.compile(r"\.(png|jpe?g|gif|svg|webp|css|js|woff2?)$|sentry|example\.|yourdomain|wixpress|domain\.com|email\.com|@2x", re.I)
PHONE_RX = re.compile(r"(?:\+|00)\s?971[\s\-\.\(\)]*\d(?:[\s\-\.\(\)]*\d){7,8}|\b800[\s\-]?\d{2,7}\b|\b0[2-9][\s\-]?\d{3}[\s\-]?\d{4}\b|\b05\d[\s\-]?\d{3}[\s\-]?\d{4}\b")
POBOX_RX = re.compile(r"p\.?\s?o\.?\s?box[:\s#\.]*\d{2,7}", re.I)
EMIRATES = [("Dubai", r"dubai"), ("Abu Dhabi", r"abu\s?dhabi"), ("Sharjah", r"sharjah"), ("Ajman", r"ajman"),
            ("Ras Al Khaimah", r"ras\s?al\s?khaimah|\brak\b"), ("Fujairah", r"fujairah"), ("Umm Al Quwain", r"umm\s?al\s?quwain|\buaq\b"),
            ("Al Ain", r"\bal\s?ain\b")]
SOCIALS = {
    "enr_linkedin_url": re.compile(r"linkedin\.com/(company|school)/[^/?#\s]+", re.I),
    "enr_twitter_url": re.compile(r"(?:twitter|x)\.com/(?!share|intent|home|search)[A-Za-z0-9_]{2,}", re.I),
    "enr_facebook_url": re.compile(r"facebook\.com/(?!sharer|share|plugins|tr\?|dialog)[^/?#\s]{2,}", re.I),
    "enr_instagram_url": re.compile(r"instagram\.com/(?!p/|explore|accounts)[A-Za-z0-9_.]{2,}", re.I),
    "enr_youtube_url": re.compile(r"youtube\.com/(?:channel|c|user|@)[^\s\"'?#]*", re.I),
}

def _clean_phone(p):
    p = re.sub(r"[^\d+]", "", p)
    if p.startswith("00971"):
        p = "+" + p[2:]
    return p

def extract(domain, pages, careers_url="", packs=None, deep=False):
    """pages: list of dicts(name,url,html,markdown,links). Returns (columns dict, text_lower, markdown_excerpt)."""
    out = {c: "" for c in COLUMNS}
    home = pages[0]
    all_html, texts, md_parts = [], [], []
    soups = []
    for i, p in enumerate(pages):
        html = p["html"] or ""
        soup = BeautifulSoup(html, "lxml") if html else BeautifulSoup("", "lxml")
        soups.append(soup)
        all_html.append(html.lower())
        s2 = BeautifulSoup(html, "lxml") if html else soup
        for t in s2(["script", "style", "noscript", "svg"]):
            t.decompose()
        texts.append(re.sub(r"\s+", " ", s2.get_text(" ", strip=True)))
        limit = (9000 if i == 0 else 6000) if deep else (6000 if i == 0 else 3500)
        if p["markdown"]:
            md_parts.append(f"## {p.get('name','page')} ({p['url']})\n" + p["markdown"][:limit])
    hs = soups[0]
    title = (hs.title.string or "").strip() if hs.title and hs.title.string else ""
    def meta(*names):
        for n in names:
            tag = hs.find("meta", attrs={"name": n}) or hs.find("meta", attrs={"property": n})
            if tag and tag.get("content"):
                return tag["content"].strip()
        return ""
    out["enr_site_title"] = (meta("og:site_name") + " | " if meta("og:site_name") and meta("og:site_name") not in title else "") + title
    out["enr_site_description"] = meta("description", "og:description")[:400]
    htag = hs.find("html")
    out["enr_site_language"] = (htag.get("lang") if htag and htag.get("lang") else "")
    full_text = " ".join(texts)
    arabic = len(re.findall(r"[؀-ۿ]", full_text))
    out["enr_arabic_site"] = bool(arabic and arabic / max(len(full_text), 1) > 0.05) or bool(hs.find("link", attrs={"hreflang": re.compile(r"^ar", re.I)}))
    blob = " ".join(all_html)
    found, cats = tech.detect(blob)
    out["enr_tech_stack"] = found
    gen = meta("generator")
    out["enr_site_cms"] = (cats.get("CMS") or [gen])[0] if (cats.get("CMS") or gen) else ""
    out["enr_ecommerce_platform"] = (cats.get("E-commerce") or [""])[0]
    out["enr_chat_support_tools"] = cats.get("Chat", []) + cats.get("Support", []) + cats.get("Messaging", [])
    out["enr_crm_marketing_tools"] = cats.get("CRM", []) + cats.get("CRM/Business suite", []) + cats.get("Marketing automation", [])
    out["enr_contact_centre_tech"] = cats.get("Contact centre", []) + cats.get("Telephony", [])
    out["enr_analytics_ads"] = cats.get("Analytics", []) + cats.get("Advertising", [])
    out["enr_hosting_cdn"] = cats.get("CDN/Hosting", [])
    out["enr_payments"] = cats.get("Payments", [])
    out["enr_ats"] = cats.get("ATS", [])
    out["enr_careers_url"] = careers_url
    out["enr_has_careers_page"] = bool(careers_url or cats.get("ATS"))
    # contacts
    mails, seen = [], set()
    for src in texts + [a["href"][7:].split("?")[0] for s in soups for a in s.find_all("a", href=True) if a["href"].lower().startswith("mailto:")]:
        for m in EMAIL_RX.findall(src):
            m = m.lower().strip(".")
            if m not in seen and not JUNK_EMAIL.search(m):
                seen.add(m); mails.append(m)
    own = [m for m in mails if m.endswith("@" + domain) or m.split("@")[1].endswith("." + domain)]
    out["enr_emails"] = (own + [m for m in mails if m not in own])[:6]
    phones, pseen = [], set()
    tel = [a["href"][4:] for s in soups for a in s.find_all("a", href=True) if a["href"].lower().startswith("tel:")]
    for src in tel + texts:
        for m in PHONE_RX.findall(src) if src not in tel else [src]:
            cp = _clean_phone(m)
            if 7 <= len(cp) <= 16 and cp not in pseen:
                pseen.add(cp); phones.append(cp)
    out["enr_phones"] = phones[:6]
    out["enr_whatsapp"] = bool(re.search(r"wa\.me/|api\.whatsapp\.com|wa\.link", blob))
    pb = POBOX_RX.search(full_text)
    out["enr_po_box"] = re.sub(r"\s+", " ", pb.group(0)) if pb else ""
    hrefs = [a["href"] for s in soups for a in s.find_all("a", href=True)]
    for col, rx in SOCIALS.items():
        for h in hrefs:
            m = rx.search(h)
            if m and "share" not in h.lower():
                out[col] = ("https://www." + m.group(0)) if not h.startswith("http") else h.split("?")[0]
                break
    # emirates: contact page weighted x3
    counts = {}
    for i, t in enumerate(texts):
        w = 3 if pages[i].get("name") == "contact" else 1
        tl = t.lower()
        for name, rx in EMIRATES:
            n = len(re.findall(rx, tl))
            if n:
                counts[name] = counts.get(name, 0) + n * w
    ranked = sorted(counts.items(), key=lambda kv: -kv[1])
    out["enr_emirates_mentioned"] = [n for n, _ in ranked]
    out["enr_hq_emirate_guess"] = ranked[0][0] if ranked else ""
    h_text = " ".join(h.get_text(" ", strip=True) for s in soups[:1] for h in s.find_all(["h1", "h2", "h3"]))
    top, ru, conf = industry.guess([(out["enr_site_title"] + " " + out["enr_site_description"], 3), (h_text, 2), (full_text[:20000], 1)])
    out["enr_industry_guess"], out["enr_industry_runner_up"], out["enr_industry_confidence"] = top, ru, conf
    out["enr_pages_crawled"] = len(pages)
    out["enr_text_chars"] = len(full_text)
    return out, full_text.lower()[:60000], "\n\n".join(md_parts)[:(max(settings.LLM_MAX_CHARS, 60000) if deep else 14000)]

async def run(domain: str, extra_pages: int = 3, client=None, focus: str = "") -> dict:
    home = None
    last_err = ""
    for url in (f"https://{domain}", f"https://www.{domain}", f"http://{domain}"):
        res = await crawl([url], client)
        r = res[0] if res else None
        if r and r["success"] and len(r["html"]) > 20 and (r["status"] or 200) < 400:
            home = r
            break
        last_err = (r["error"] if r else "") or (f"status {r['status']}" if r and r["status"] else "no result")
    out = {c: "" for c in COLUMNS}
    if not home:
        out["enr_web_status"] = "unreachable"
        out["_error"] = f"website unreachable: {last_err[:120]}"
        return out
    final = home["final_url"] or home["url"]
    pages = [{**home, "name": "home"}]
    if focus == "partner":
        want, more = min(MAX_EXTRA_PAGES_PARTNER, max(extra_pages, MIN_EXTRA_PAGES_PARTNER)), None
        n_links = len((home["links"] or {}).get("internal") or [])
        if n_links < 25:  # thin or script-built menu: look at the sitemap as well
            more = await sitemap_urls(final)
    else:
        want, more = max(0, min(extra_pages, MAX_EXTRA_PAGES_FOCUS if focus in FOCUS_CATS else MAX_EXTRA_PAGES)), None
    urls, careers_url = pick_pages(final, home["links"], want, focus, more)
    if urls:
        extra = await crawl(urls, client)
        for u, r in zip(urls, extra):
            if r["success"] and r["html"]:
                nm = next((n for n, rx in ALL_CATS if rx.search(urlparse(u).path)), "page")
                pages.append({**r, "name": nm})
    cols, text, md = extract(domain, pages, careers_url, deep=focus in FOCUS_CATS)
    cols["enr_web_final_url"] = final
    cols["enr_web_status"] = "blocked" if _looks_blocked(home["html"], text) else "ok"
    cols["_text"] = text
    cols["_md"] = md
    return cols
