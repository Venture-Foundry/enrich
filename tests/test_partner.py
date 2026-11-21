"""Partner type list, deeper partner crawl, sitemap fallback, driving-school sector rule."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from app.steps import llm_step as L, web_step as W

FAIL = 0
def check(name, cond, extra=""):
    global FAIL
    if cond: print("  ok  ", name)
    else: FAIL += 1; print("  FAIL", name, extra)

# partner types
check("exact type kept", L._ptype("Managed service provider") == "Managed service provider")
check("case-insensitive prefix", L._ptype("cybersecurity") == "Cybersecurity & MSSP", L._ptype("cybersecurity"))
check("not-a-partner value", L._ptype("not a technology partner") == "Not a technology partner")
check("off-list kept visible", L._ptype("Insurance company") == "Other (Insurance company)")
check("empty stays empty", L._ptype("") == "")
check("type list has the escape values", "Not a technology partner" in L.PARTNER_TYPES and "Other" in L.PARTNER_TYPES)
m = L._map("partner", {"partner_type": "Insurance", "secondary_types": ["Managed service provider", "Cloud"], "case_studies": [{"customer": "A", "delivered": "SOC"}], "capability_depth": ["40 engineers"], "target_customers": ["banks"], "partner_type_evidence": "x"})
check("new partner columns mapped", m["enr_p_case_studies"] == ["A - SOC"] and m["enr_p_capability_depth"] == ["40 engineers"] and m["enr_p_target_customers"] == ["banks"] and m["enr_p_type_evidence"] == "x", m)
check("every partner column is produced", set(L.PARTNER_COLUMNS) <= set(L._map("partner", {}).keys()), set(L.PARTNER_COLUMNS) - set(L._map("partner", {}).keys()))
check("prompt renders", "Not a technology partner" in (L.PROMPTS["partner"] % {"rules": L.RULES, "domain": "x.ae", "text": "t", "type_defs": L.PARTNER_TYPE_PROMPT, "type_rules": L.PARTNER_TYPE_RULES}))

# education / driving school
check("education covers driving schools", "driving" in L.SECTOR_DEFS["Education & training"])
check("rules steer away from Other", "driving school" in L.SECTOR_RULES)
check("version bumped so profiles refresh", L.TAXONOMY_VERSION == "t4")

# pages: two per category for partners, microsoft page gets its own slot
LINKS = {"internal": [{"href": h, "text": t} for h, t in (
    ("/contact", "Contact"), ("/about", "About"), ("/careers", "Careers"),
    ("/services/managed-it", "Managed IT"), ("/services/cyber", "Cyber services"), ("/solutions/cloud", "Cloud solutions"),
    ("/partners", "Technology partners"), ("/microsoft", "Microsoft practice"), ("/industries", "Industries"),
    ("/case-studies", "Case studies"), ("/certifications", "Certifications"))], "external": []}
p, c = W.pick_pages("https://x.ae", LINKS, 12, "partner")
check("two service pages read", sum("/services/" in u for u in p) == 2, p)
check("microsoft practice page read", any("/microsoft" in u for u in p), p)
check("no duplicate pages", len(p) == len(set(p)), p)
check("cap respected", len(W.pick_pages("https://x.ae", LINKS, 5, "partner")[0]) <= 5)
cust, _ = W.pick_pages("https://x.ae", LINKS, 8, "customer")
check("customer crawl unchanged: one page per category", sum("/services/" in u for u in cust) <= 1, cust)
check("careers url still reported", c.endswith("/careers"))

# sitemap
xml = "<urlset><url><loc>https://x.ae/</loc></url><url><loc>https://x.ae/solutions/sap</loc></url><url><loc>https://x.ae/logo.png</loc></url><url><loc>https://x.ae/ar/about</loc></url><url><loc>https://x.ae/case-studies/bank</loc></url></urlset>"
pages, kids = W.parse_sitemap(xml)
check("sitemap keeps real pages, drops images and other languages", pages == ["https://x.ae/", "https://x.ae/solutions/sap", "https://x.ae/case-studies/bank"], pages)
idx = "<sitemapindex><sitemap><loc>https://x.ae/page-sitemap.xml</loc></sitemap></sitemapindex>"
check("sitemap index returns children", W.parse_sitemap(idx) == ([], ["https://x.ae/page-sitemap.xml"]))
thin = {"internal": [{"href": "/contact", "text": "Contact"}], "external": []}
p2, _ = W.pick_pages("https://x.ae", thin, 10, "partner", more=pages)
check("thin menu filled from sitemap", any("/solutions/sap" in u for u in p2) and any("/case-studies/" in u for u in p2), p2)
check("sitemap URLs on other hosts ignored", W.pick_pages("https://x.ae", thin, 10, "partner", more=["https://evil.com/services"])[0] == [] or all("evil" not in u for u in W.pick_pages("https://x.ae", thin, 10, "partner", more=["https://evil.com/services"])[0]))

print("FAILED" if FAIL else "ALL PASSED", FAIL)
sys.exit(1 if FAIL else 0)
