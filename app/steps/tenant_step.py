"""Public Microsoft Entra ID (Azure AD) tenant lookup by domain. Unauthenticated metadata only."""
import re
import httpx

COLUMNS = ["enr_m365_tenant", "enr_m365_namespace", "enr_m365_federation_brand", "enr_m365_tenant_id"]
_transport = None  # tests may inject an httpx transport

async def run(domain: str) -> dict:
    out = {c: "" for c in COLUMNS}
    async with httpx.AsyncClient(timeout=10, transport=_transport, headers={"User-Agent": "enrich-portal/1.0"}) as c:
        try:
            r = await c.get("https://login.microsoftonline.com/getuserrealm.srf",
                            params={"login": f"user@{domain}", "json": "1"})
            if r.status_code == 200:
                j = r.json()
                ns = j.get("NameSpaceType", "")
                out["enr_m365_namespace"] = ns
                out["enr_m365_federation_brand"] = j.get("FederationBrandName", "") if ns != "Unknown" else ""
            r2 = await c.get(f"https://login.microsoftonline.com/{domain}/v2.0/.well-known/openid-configuration")
            if r2.status_code == 200:
                iss = r2.json().get("issuer", "")
                m = re.search(r"microsoftonline\.com/([0-9a-f-]{36})", iss)
                if m:
                    out["enr_m365_tenant_id"] = m.group(1)
        except (httpx.HTTPError, ValueError) as e:
            raise RuntimeError(f"tenant lookup failed: {type(e).__name__}")
    out["enr_m365_tenant"] = bool(out["enr_m365_tenant_id"]) or out["enr_m365_namespace"] in ("Managed", "Federated")
    return out
