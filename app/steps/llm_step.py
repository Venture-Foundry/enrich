"""Optional LLM step (OpenAI-compatible /chat/completions). Off unless LLM_BASE_URL + LLM_MODEL are set."""
import json, re
import httpx
from .. import settings

COLUMNS = ["enr_llm_one_liner", "enr_llm_offerings", "enr_llm_sector", "enr_llm_customer_type", "enr_llm_hq_emirate", "enr_llm_key_people", "enr_llm_confidence"]
_transport = None

PROMPT = """You are enriching a B2B company record from its website text. Use ONLY the text provided. If something is not stated, use "" (or [] for lists). Do not guess names.
Return a single JSON object with exactly these keys:
one_liner (max 25 words, what the company does), offerings (list of up to 6 products/services), sector (short label),
customer_type ("B2B", "B2C", "B2B2C" or ""), hq_emirate (UAE emirate of the head office if stated), key_people (list of up to 5 objects {name,title} only if explicitly named with a title),
confidence ("high", "medium" or "low").
Company domain: %s
Website text:
%s"""

async def run(domain: str, markdown: str) -> dict:
    if not settings.LLM_ENABLED:
        raise RuntimeError("LLM not configured")
    if not markdown:
        raise RuntimeError("no website text to summarise (run the website step first)")
    body = {"model": settings.LLM_MODEL, "temperature": 0, "messages": [{"role": "user", "content": PROMPT % (domain, markdown[:9000])}],
            "response_format": {"type": "json_object"}}
    headers = {"Authorization": f"Bearer {settings.LLM_API_KEY}"} if settings.LLM_API_KEY else {}
    async with httpx.AsyncClient(timeout=120, transport=_transport) as c:
        try:
            r = await c.post(f"{settings.LLM_BASE_URL}/chat/completions", json=body, headers=headers)
            if r.status_code == 400:  # some servers reject response_format
                body.pop("response_format", None)
                r = await c.post(f"{settings.LLM_BASE_URL}/chat/completions", json=body, headers=headers)
            r.raise_for_status()
            text = r.json()["choices"][0]["message"]["content"]
        except (httpx.HTTPError, KeyError, ValueError) as e:
            raise RuntimeError(f"LLM call failed: {type(e).__name__}")
    m = re.search(r"\{.*\}", text, re.S)
    try:
        j = json.loads(m.group(0)) if m else {}
    except ValueError:
        raise RuntimeError("LLM returned non-JSON")
    people = j.get("key_people") or []
    return {
        "enr_llm_one_liner": j.get("one_liner", ""), "enr_llm_offerings": j.get("offerings") or [], "enr_llm_sector": j.get("sector", ""),
        "enr_llm_customer_type": j.get("customer_type", ""), "enr_llm_hq_emirate": j.get("hq_emirate", ""),
        "enr_llm_key_people": [f"{p.get('name','')} ({p.get('title','')})" for p in people if isinstance(p, dict) and p.get("name")],
        "enr_llm_confidence": j.get("confidence", ""),
    }
