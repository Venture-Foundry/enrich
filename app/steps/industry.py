"""Rule-based industry guess (no LLM) from website text, plus NAICS sector from Apollo codes."""
import re

NAICS2 = {
    "11": "Agriculture", "21": "Mining, oil & gas", "22": "Utilities", "23": "Construction", "31": "Manufacturing",
    "32": "Manufacturing", "33": "Manufacturing", "42": "Wholesale trade", "44": "Retail", "45": "Retail",
    "48": "Transportation & logistics", "49": "Transportation & logistics", "51": "Information & media",
    "52": "Finance & insurance", "53": "Real estate & rental", "54": "Professional & technical services",
    "55": "Holding companies", "56": "Administrative & support services", "61": "Education", "62": "Healthcare",
    "71": "Arts, entertainment & recreation", "72": "Accommodation & food services", "81": "Other services",
    "92": "Public administration",
}

BUCKETS = {
    "Real estate & property": ["real estate", "properties", "property management", "off-plan", "developer", "leasing", "brokerage", "villas", "apartments", "community", "mortgage"],
    "Construction & engineering": ["construction", "contracting", "civil", "mep", "engineering", "infrastructure", "fit-out", "fitout", "interior design", "steel", "structural", "consultancy engineering"],
    "Oil, gas & energy": ["oil & gas", "oil and gas", "petroleum", "lng", "power plant", "solar", "renewable", "utilities", "energy", "pipeline", "refinery", "drilling"],
    "Hospitality & tourism": ["hotel", "resort", "hospitality", "tourism", "travel agency", "restaurant", "catering", "f&b", "cafe", "holiday", "tours"],
    "Retail & e-commerce": ["retail", "e-commerce", "ecommerce", "add to cart", "shop now", "boutique", "supermarket", "hypermarket", "fashion", "stores", "online store"],
    "Healthcare & pharma": ["hospital", "clinic", "medical", "healthcare", "pharma", "dental", "diagnostics", "patient", "physician", "wellness"],
    "Education": ["school", "university", "college", "academy", "curriculum", "students", "nursery", "admissions", "campus"],
    "Financial services": ["bank", "insurance", "investment", "wealth", "fintech", "exchange house", "takaful", "brokerage firm", "lending", "asset management", "finance"],
    "Logistics & transport": ["logistics", "freight", "shipping", "cargo", "transport", "supply chain", "warehouse", "courier", "fleet", "forwarding", "customs clearance"],
    "Technology & IT services": ["software", "cloud", "it services", "digital transformation", "cybersecurity", "saas", "managed services", "data center", "erp", "artificial intelligence", "systems integrator", "web development", "app development"],
    "Telecom": ["telecom", "telecommunications", "mobile network", "broadband", "isp", "voip", "connectivity"],
    "Manufacturing & industrial": ["manufacturing", "factory", "industrial", "fabrication", "production line", "plastics", "chemicals", "packaging", "machinery"],
    "Food & beverage / FMCG": ["food", "beverage", "fmcg", "bakery", "dairy", "juice", "snacks", "grocery", "frozen", "poultry"],
    "Automotive": ["automotive", "dealership", "vehicles", "spare parts", "motors", "car rental", "tyres", "garage"],
    "Government & public sector": ["ministry", "authority", "municipality", "government", "department of", "federal", "emirate of"],
    "Professional services": ["consulting", "advisory", "law firm", "legal services", "audit", "accounting", "recruitment", "staffing", "hr services", "business setup", "pro services"],
    "Media & marketing": ["marketing agency", "advertising", "media", "branding", "public relations", "creative agency", "production house", "social media"],
    "Facilities & services": ["facilities management", "cleaning", "security services", "manpower", "outsourcing", "pest control", "maintenance services", "hvac"],
    "Aviation & maritime": ["airline", "airport", "aviation", "aircraft", "maritime", "shipyard", "vessels", "ports"],
    "Sports, leisure & entertainment": ["entertainment", "fitness", "gym", "theme park", "events", "sports", "leisure", "cinema"],
}

def naics_sector(naics_codes: str):
    for code in re.findall(r"\d{2,6}", naics_codes or ""):
        s = NAICS2.get(code[:2])
        if s:
            return s
    return ""

def guess(weighted_texts):
    """weighted_texts: list of (text, weight). Returns (guess, runner_up, confidence)."""
    scores = {}
    for text, w in weighted_texts:
        t = " " + (text or "").lower() + " "
        for bucket, kws in BUCKETS.items():
            n = 0
            for k in kws:
                n += len(re.findall(r"(?<![a-z])" + re.escape(k) + r"(?![a-z])", t))
            if n:
                scores[bucket] = scores.get(bucket, 0) + min(n, 8) * w
    if not scores:
        return "", "", "none"
    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    top, ts = ranked[0]
    ru, rs = ranked[1] if len(ranked) > 1 else ("", 0)
    if ts >= 12 and ts >= 1.6 * rs:
        conf = "high"
    elif ts >= 6 and ts > rs:
        conf = "medium"
    else:
        conf = "low"
    return top, ru, conf
