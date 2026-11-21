"""DNS-based footprint: mail provider, Microsoft 365 / Teams-SIP evidence, SaaS verification records."""
import asyncio, re
import dns.asyncresolver, dns.exception, dns.resolver
from .. import settings

COLUMNS = [
    "enr_dns_status", "enr_mx_provider", "enr_mx_hosts", "enr_mail_gateway", "enr_m365_evidence", "enr_google_workspace",
    "enr_teams_sip_dns", "enr_teams_sip_evidence", "enr_spf_includes", "enr_dmarc_policy", "enr_ns_provider",
    "enr_www_cname", "enr_txt_vendors", "enr_has_ipv6",
]

MX_PROVIDERS = [
    ("mail.protection.outlook.com", "Microsoft 365"), ("outlook.com", "Microsoft 365"),
    ("aspmx.l.google.com", "Google Workspace"), ("googlemail.com", "Google Workspace"), ("google.com", "Google Workspace"),
    ("pphosted.com", "Proofpoint"), ("ppe-hosted.com", "Proofpoint"), ("mimecast", "Mimecast"),
    ("zoho.", "Zoho Mail"), ("secureserver.net", "GoDaddy"), ("protonmail", "Proton"), ("messagelabs.com", "Symantec/Broadcom"),
    ("barracudanetworks.com", "Barracuda"), ("trendmicro", "Trend Micro"), ("iphmx.com", "Cisco Email Security"),
    ("yandex", "Yandex"), ("icloud.com", "Apple iCloud"), ("mailgun", "Mailgun"), ("sophos", "Sophos"),
    ("forcepoint", "Forcepoint"), ("fireeyecloud.com", "Trellix"), ("hornetsecurity", "Hornetsecurity"),
    ("emirates.net.ae", "Etisalat/e&"), ("eim.ae", "du/EITC"), ("hostinger", "Hostinger"), ("titan.email", "Titan"),
]
GATEWAYS = {"Proofpoint", "Mimecast", "Symantec/Broadcom", "Barracuda", "Trend Micro", "Cisco Email Security",
            "Sophos", "Forcepoint", "Trellix", "Hornetsecurity"}

NS_PROVIDERS = [
    ("cloudflare.com", "Cloudflare"), ("awsdns", "AWS Route 53"), ("azure-dns", "Azure DNS"), ("domaincontrol.com", "GoDaddy"),
    ("googledomains.com", "Google Domains"), ("google.com", "Google Cloud DNS"), ("nsone.net", "NS1"),
    ("registrar-servers.com", "Namecheap"), ("wixdns.net", "Wix"), ("squarespace", "Squarespace"), ("ultradns", "UltraDNS"),
    ("dnsmadeeasy", "DNS Made Easy"), ("akam.net", "Akamai"), ("etisalat", "Etisalat/e&"), ("eim.ae", "du/EITC"),
    ("hostgator", "HostGator"), ("bluehost", "Bluehost"), ("name.com", "Name.com"), ("gandi", "Gandi"),
]

# Prefix/substring in a TXT record -> SaaS vendor it reveals
TXT_VENDORS = [
    ("ms=ms", "Microsoft 365"), ("v=spf1 include:spf.protection.outlook.com", "Microsoft 365"),
    ("google-site-verification", "Google (Workspace/Search Console)"), ("atlassian-domain-verification", "Atlassian"),
    ("facebook-domain-verification", "Meta/Facebook"), ("docusign", "DocuSign"), ("zoom-domain-verification", "Zoom"),
    ("adobe-idp-site-verification", "Adobe"), ("apple-domain-verification", "Apple"), ("stripe-verification", "Stripe"),
    ("hubspot", "HubSpot"), ("salesforce", "Salesforce"), ("pardot", "Salesforce Pardot"), ("mailchimp", "Mailchimp"),
    ("sendgrid", "SendGrid"), ("mandrill", "Mailchimp Mandrill"), ("amazonses", "Amazon SES"), ("zendesk", "Zendesk"),
    ("freshdesk", "Freshworks"), ("slack-domain-verification", "Slack"), ("miro-verification", "Miro"),
    ("notion-domain-verification", "Notion"), ("asana-verification", "Asana"), ("onetrust", "OneTrust"),
    ("canva-site-verification", "Canva"), ("dropbox-domain-verification", "Dropbox"), ("box-domain-verification", "Box"),
    ("webex-domain-verification", "Webex"), ("cisco-ci-domain-verification", "Cisco Webex"), ("citrix", "Citrix"),
    ("okta", "Okta"), ("have-i-been-pwned-verification", "Have I Been Pwned"), ("globalsign-domain-verification", "GlobalSign"),
    ("ringcentral", "RingCentral"), ("8x8", "8x8"), ("twilio-domain-verification", "Twilio"), ("genesys", "Genesys"),
    ("smartsheet", "Smartsheet"), ("monday", "monday.com"), ("workplace-domain-verification", "Meta Workplace"),
    ("shopify", "Shopify"), ("klaviyo", "Klaviyo"), ("proofpoint", "Proofpoint"), ("mimecast", "Mimecast"),
    ("knowbe4", "KnowBe4"), ("cloudflare-verify", "Cloudflare"), ("_github-challenge", "GitHub"), ("postman", "Postman"),
    ("sophos", "Sophos"), ("tiktok-developers-site-verification", "TikTok"), ("pinterest-site-verification", "Pinterest"),
]

def _match(text, table):
    t = text.lower()
    for needle, label in table:
        if needle in t:
            return label
    return None

_resolver = None
_sem = None

def _get():
    global _resolver, _sem
    if _resolver is None:
        _resolver = dns.asyncresolver.Resolver()
        if settings.DNS_SERVERS:
            _resolver.nameservers = settings.DNS_SERVERS
        _resolver.lifetime = 6.0
        _resolver.timeout = 3.0
        _sem = asyncio.Semaphore(settings.DNS_CONCURRENCY)
    return _resolver, _sem

class DnsLookupError(Exception):
    pass

async def _q(name, rtype, resolver=None):
    """Return list of text answers; [] when no such record; raise DnsLookupError on timeouts/servfail."""
    r, sem = _get()
    r = resolver or r
    async with sem:
        try:
            ans = await r.resolve(name, rtype)
            return [x.to_text() for x in ans]
        except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
            return []
        except dns.resolver.NoNameservers:
            raise DnsLookupError("servfail")
        except (dns.exception.Timeout, OSError) as e:
            raise DnsLookupError(f"timeout/{type(e).__name__}")
        except dns.exception.DNSException as e:
            return []

def derive(rec: dict) -> dict:
    """Pure function: turn raw record dict into enrichment columns (unit-testable offline)."""
    out = {c: "" for c in COLUMNS}
    mx = rec.get("MX", [])
    mx_hosts = []
    for line in sorted(mx, key=lambda s: int(s.split()[0]) if s.split()[0].isdigit() else 99):
        parts = line.split()
        mx_hosts.append(parts[-1].rstrip(".").lower())
    out["enr_mx_hosts"] = mx_hosts
    providers = []
    for h in mx_hosts:
        p = _match(h, MX_PROVIDERS)
        providers.append(p or "Other")
    gws = [p for p in providers if p in GATEWAYS]
    non_gw = [p for p in providers if p not in GATEWAYS and p != "Other"]
    gateway = gws[0] if gws else ""
    txt = [t.strip('"').replace('" "', "") for t in rec.get("TXT", [])]
    spf = next((t for t in txt if t.lower().startswith("v=spf1")), "")
    includes = re.findall(r"include:([^\s]+)", spf.lower())
    out["enr_spf_includes"] = includes
    evidence = []
    if any("mail.protection.outlook.com" in h for h in mx_hosts): evidence.append("MX")
    if "spf.protection.outlook.com" in spf.lower(): evidence.append("SPF")
    if any("autodiscover.outlook.com" in c.lower() for c in rec.get("autodiscover", [])): evidence.append("autodiscover")
    if any(t.lower().startswith("ms=ms") for t in txt): evidence.append("MS TXT verification")
    if any("onmicrosoft.com" in c.lower() for c in rec.get("selector1", []) + rec.get("selector2", [])): evidence.append("DKIM selector")
    if any("outlook.com" in c.lower() or "office365" in c.lower() or "microsoftonline" in c.lower() for c in rec.get("enterpriseregistration", []) + rec.get("enterpriseenrollment", [])): evidence.append("Entra/Intune enrollment CNAME")
    out["enr_m365_evidence"] = evidence
    google_spf = "_spf.google.com" in spf.lower()
    if non_gw:
        primary = non_gw[0]
    elif not providers:
        primary = "Microsoft 365 (no MX found)" if evidence else "No MX"
    elif evidence:
        primary = "Microsoft 365 (behind gateway)" if gws else "Microsoft 365"
    elif google_spf:
        primary = "Google Workspace (behind gateway)" if gws else "Google Workspace"
    elif gws:
        primary = f"{gws[0]} (gateway; backend unknown)"
    else:
        primary = "Other"
    out["enr_mx_provider"] = primary
    out["enr_mail_gateway"] = gateway
    out["enr_google_workspace"] = ("google" in primary.lower()) or ("_spf.google.com" in spf.lower())
    sip_ev = []
    for key, label in (("sip", "sip CNAME"), ("lyncdiscover", "lyncdiscover CNAME"), ("srv_sip_tls", "_sip._tls SRV"),
                       ("srv_sipfed", "_sipfederationtls SRV")):
        vals = rec.get(key, [])
        if any("lync.com" in v.lower() or "skype" in v.lower() or "teams" in v.lower() or "microsoft" in v.lower() for v in vals):
            sip_ev.append(label + " -> Microsoft")
        elif vals:
            sip_ev.append(label + " (non-Microsoft)")
    out["enr_teams_sip_evidence"] = sip_ev
    out["enr_teams_sip_dns"] = any("-> Microsoft" in e for e in sip_ev)
    pol = ""
    for t in rec.get("dmarc", []):
        m = re.search(r"\bp=(\w+)", t.lower())
        if m: pol = m.group(1)
    out["enr_dmarc_policy"] = pol or "none"
    ns = [n.rstrip(".").lower() for n in rec.get("NS", [])]
    out["enr_ns_provider"] = next((_match(n, NS_PROVIDERS) for n in ns if _match(n, NS_PROVIDERS)), "Other" if ns else "")
    out["enr_www_cname"] = [c.rstrip(".").lower() for c in rec.get("www", [])]
    vendors = []
    for t in txt:
        v = _match(t, TXT_VENDORS)
        if v and v not in vendors: vendors.append(v)
    for inc in includes:
        v = _match("v=spf1 include:" + inc, TXT_VENDORS) or _match(inc, TXT_VENDORS)
        if v and v not in vendors: vendors.append(v)
    out["enr_txt_vendors"] = vendors
    out["enr_has_ipv6"] = bool(rec.get("AAAA"))
    out["enr_dns_status"] = "ok"
    return out

async def run(domain: str, resolver=None) -> dict:
    names = {
        "A": (domain, "A"), "AAAA": (domain, "AAAA"), "MX": (domain, "MX"), "TXT": (domain, "TXT"), "NS": (domain, "NS"),
        "dmarc": ("_dmarc." + domain, "TXT"), "autodiscover": ("autodiscover." + domain, "CNAME"),
        "lyncdiscover": ("lyncdiscover." + domain, "CNAME"), "sip": ("sip." + domain, "CNAME"),
        "srv_sip_tls": ("_sip._tls." + domain, "SRV"), "srv_sipfed": ("_sipfederationtls._tcp." + domain, "SRV"),
        "enterpriseregistration": ("enterpriseregistration." + domain, "CNAME"),
        "enterpriseenrollment": ("enterpriseenrollment." + domain, "CNAME"),
        "selector1": ("selector1._domainkey." + domain, "CNAME"), "selector2": ("selector2._domainkey." + domain, "CNAME"),
        "www": ("www." + domain, "CNAME"),
    }
    keys = list(names)
    results = await asyncio.gather(*[_q(names[k][0], names[k][1], resolver) for k in keys], return_exceptions=True)
    rec, errs = {}, []
    for k, r in zip(keys, results):
        if isinstance(r, Exception):
            errs.append(k)
            rec[k] = []
        else:
            rec[k] = r
    core_failed = [k for k in ("A", "MX", "TXT", "NS") if k in errs]
    if len(core_failed) == 4:
        raise DnsLookupError("DNS lookups timed out (check DNS_SERVERS / network)")
    if not any(rec.get(k) for k in ("A", "AAAA", "MX", "NS", "TXT")):
        out = {c: "" for c in COLUMNS}
        out["enr_dns_status"] = "no records (domain may not exist)"
        return out
    out = derive(rec)
    if errs:
        out["enr_dns_status"] = "partial (" + ",".join(errs[:4]) + " timed out)"
    return out
