"""Tiny stand-in for crawl4ai's POST /crawl and GET /health, with canned sites. Test use only."""
import os
from urllib.parse import urlparse
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

app = FastAPI()
CALLS = []

def page(title, desc, body, head_extra=""):
    return f"""<html lang="en"><head><title>{title}</title><meta name="description" content="{desc}">{head_extra}</head><body>
<nav><a href="/">Home</a> <a href="/about-us">About us</a> <a href="/contact-us">Contact</a> <a href="/careers">Careers</a></nav>{body}</body></html>"""

SITES = {
 "acmehotels.ae": {
   "/": page("Acme Hotels & Resorts", "Luxury hotel and resort group with restaurants across Dubai", """
      <h1>Hotels and resorts in Dubai</h1><p>Our hotel collection, restaurant and catering services, tourism and holiday packages.</p>
      <a href="https://www.linkedin.com/company/acme-hotels">LinkedIn</a><a href="https://www.facebook.com/acmehotels">FB</a><a href="https://twitter.com/intent/tweet?x=1">share</a>
      <a href="https://wa.me/97150000000">chat</a><script src="/wp-content/themes/x/app.js"></script><script src="https://www.googletagmanager.com/gtag/js?id=G-AAA"></script>
      <script src="https://widget.intercom.io/widget/abc"></script>"""),
   "/contact-us": page("Contact Acme", "", "<p>Head office: P.O. Box 12345, Dubai, UAE. Branch in Abu Dhabi.</p><a href='mailto:reservations@acmehotels.ae'>mail</a> <a href='tel:+97141234567'>call</a> Call us +971 4 123 4567 or 800 4567. Dubai Dubai"),
   "/about-us": page("About", "", "<p>Founded in Dubai, we run hotels.</p>"),
   "/careers": page("Careers", "", "<a href='https://jobs.lever.co/acmehotels'>open roles</a>"),
 },
 "callcentreco.ae": {
   "/": page("CallCentreCo - Customer Care", "Outsourced contact centre and call centre services, 24/7 support", """
      <h1>Contact centre services</h1><p>Omnichannel customer service, IVR, helpline and hotline operations. Customer support 24/7 support.</p>
      <script src="https://apps.mypurecloud.com/widgets/x.js"></script><script src="https://static.zdassets.com/ekr/snippet.js"></script>"""),
 },
 "freshbeauty.ae": {
   "/": page("Fresh Beauty Store", "Online store for makeup, skincare, fragrance and sunglasses", """
      <h1>Beauty store</h1><p>Shop cosmetics, makeup, skincare and eyewear. Add to cart.</p><script src="https://cdn.shopify.com/s/files/x.js"></script>"""),
 },
 "blocked.ae": {"/": "<html><head><title>Just a moment...</title></head><body>Just a moment... Checking your browser. Please enable JavaScript and cookies to continue</body></html>"},
}

def generic(host):
    return page(f"{host} home", "Software and cloud services", f"<h1>{host}</h1><p>Software, cloud and IT services company in Sharjah.</p>")

@app.get("/health")
async def health():
    return {"status": "ok", "mock": True}

@app.post("/crawl")
async def crawl(req: Request):
    body = await req.json()
    CALLS.append(body)
    if os.getenv("MOCK_STRICT") == "minimal" and ("browser_config" in body or "crawler_config" in body):
        return JSONResponse({"detail": "unsupported config"}, status_code=422)
    results = []
    for u in body["urls"]:
        p = urlparse(u)
        host = (p.hostname or "").removeprefix("www.")
        if host == "down.ae" or (host.startswith("www.") is False and p.scheme == "http" and host == "httponly.ae" and False):
            results.append({"url": u, "success": False, "error_message": "net::ERR_NAME_NOT_RESOLVED", "status_code": None})
            continue
        if host == "slow.ae":
            import asyncio; await asyncio.sleep(0.4)
        site = SITES.get(host)
        path = p.path or "/"
        html = (site.get(path) if site else None) or (generic(host) if (path == "/" and not site) else None)
        if html is None:
            results.append({"url": u, "success": False, "error_message": "404", "status_code": 404})
            continue
        links = {"internal": [{"href": h, "text": t} for h, t in (("/about-us", "About us"), ("/contact-us", "Contact"), ("/careers", "Careers"))], "external": []}
        results.append({"url": u, "redirected_url": u, "success": True, "status_code": 200, "html": html, "cleaned_html": html,
                        "markdown": {"raw_markdown": "# " + host + "\n" + "text " * 50}, "links": links, "metadata": {}})
    return {"success": True, "results": results}
