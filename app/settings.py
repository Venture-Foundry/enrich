import json, os

def _int(name, default):
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default

DATA_DIR = os.getenv("DATA_DIR", "./data")
CRAWL4AI_URL = os.getenv("CRAWL4AI_URL", "http://192.168.210.10:11235").rstrip("/")
CRAWL4AI_TOKEN = os.getenv("CRAWL4AI_TOKEN", "")
PORTAL_USER = os.getenv("PORTAL_USER", "")
PORTAL_PASSWORD = os.getenv("PORTAL_PASSWORD", "")
CRAWL_CONCURRENCY = _int("CRAWL_CONCURRENCY", 3)
ROW_CONCURRENCY = _int("ROW_CONCURRENCY", 8)
DNS_CONCURRENCY = _int("DNS_CONCURRENCY", 40)
DNS_SERVERS = [s.strip() for s in os.getenv("DNS_SERVERS", "").split(",") if s.strip()]
CACHE_TTL_DAYS = _int("CACHE_TTL_DAYS", 30)
CRAWL_PAGE_TIMEOUT_MS = _int("CRAWL_PAGE_TIMEOUT_MS", 30000)
CRAWL_HTTP_TIMEOUT_S = _int("CRAWL_HTTP_TIMEOUT_S", 90)
# Optional JSON overrides if your crawl4ai version expects different config shapes
CRAWL4AI_BROWSER_CONFIG = os.getenv("CRAWL4AI_BROWSER_CONFIG", "")
CRAWL4AI_CRAWLER_CONFIG = os.getenv("CRAWL4AI_CRAWLER_CONFIG", "")
# LLM_PROVIDER: "openai" (any OpenAI-compatible /chat/completions endpoint: OpenRouter, Ollama, ...) or "anthropic" (native Messages API)
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "openai").strip().lower()
LLM_BASE_URL = (os.getenv("LLM_BASE_URL", "") or ("https://api.anthropic.com" if LLM_PROVIDER == "anthropic" else "")).rstrip("/")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "")
LLM_ENABLED = bool(LLM_BASE_URL and LLM_MODEL)
# Partner / customer focus: how much crawled text is sent to the LLM per company, and the max reply size
LLM_MAX_CHARS = _int("LLM_MAX_CHARS", 30000)
LLM_MAX_TOKENS = _int("LLM_MAX_TOKENS", 3000)
LLM_TIMEOUT_S = _int("LLM_TIMEOUT_S", 180)
# Extra JSON merged into every LLM request body, e.g. for OpenRouter: {"provider": {"data_collection": "deny", "only": ["..."]}, "reasoning": {"enabled": false}}
def _json_obj(name):
    raw = os.getenv(name, "").strip()
    if not raw:
        return {}
    try:
        v = json.loads(raw)
        return v if isinstance(v, dict) else {}
    except ValueError:
        return {}
LLM_EXTRA_BODY = _json_obj("LLM_EXTRA_BODY")


# ---- LLM settings editable from the portal (saved in DATA_DIR/llm_settings.json; override the env values) ----
LLM_PRESETS = {
    "openrouter": {"label": "OpenRouter", "provider": "openai", "base_url": "https://openrouter.ai/api/v1"},
    "anthropic": {"label": "Anthropic (Claude API)", "provider": "anthropic", "base_url": "https://api.anthropic.com"},
    "gemini": {"label": "Google Gemini (OpenAI-compatible)", "provider": "openai", "base_url": "https://generativelanguage.googleapis.com/v1beta/openai"},
    "custom": {"label": "Custom / local (OpenAI-compatible)", "provider": "openai", "base_url": ""},
}

def _llm_file():
    return os.path.join(DATA_DIR, "llm_settings.json")

def _apply_llm(c):
    global LLM_PROVIDER, LLM_BASE_URL, LLM_API_KEY, LLM_MODEL, LLM_ENABLED, LLM_MAX_CHARS, LLM_MAX_TOKENS, LLM_EXTRA_BODY
    LLM_PROVIDER = (c.get("provider") or "openai").strip().lower()
    LLM_BASE_URL = ((c.get("base_url") or "") or ("https://api.anthropic.com" if LLM_PROVIDER == "anthropic" else "")).strip().rstrip("/")
    LLM_API_KEY = (c.get("api_key") or "").strip()
    LLM_MODEL = (c.get("model") or "").strip()
    LLM_MAX_CHARS = int(c.get("max_chars") or 30000)
    LLM_MAX_TOKENS = int(c.get("max_tokens") or 3000)
    eb = c.get("extra_body")
    LLM_EXTRA_BODY = eb if isinstance(eb, dict) else {}
    LLM_ENABLED = bool(LLM_BASE_URL and LLM_MODEL)

def llm_current():
    return {"provider": LLM_PROVIDER, "base_url": LLM_BASE_URL, "api_key": LLM_API_KEY, "model": LLM_MODEL,
            "max_chars": LLM_MAX_CHARS, "max_tokens": LLM_MAX_TOKENS, "extra_body": LLM_EXTRA_BODY}

def llm_public():
    """What the browser may see: everything except the API key (only whether one is set, and its last 4 characters)."""
    key = LLM_API_KEY
    return {"provider": LLM_PROVIDER, "base_url": LLM_BASE_URL, "model": LLM_MODEL, "api_key_set": bool(key), "api_key_hint": ("..." + key[-4:]) if len(key) >= 12 else "",
            "extra_body": json.dumps(LLM_EXTRA_BODY) if LLM_EXTRA_BODY else "", "max_chars": LLM_MAX_CHARS, "max_tokens": LLM_MAX_TOKENS,
            "enabled": LLM_ENABLED, "source": "portal" if os.path.exists(_llm_file()) else "env", "auth_enabled": bool(PORTAL_USER and PORTAL_PASSWORD),
            "presets": {k: {"label": v["label"], "provider": v["provider"], "base_url": v["base_url"]} for k, v in LLM_PRESETS.items()}}

def save_llm(update: dict):
    """Merge `update` onto the current settings, validate, persist (0600) and apply. api_key: omitted/blank keeps the saved key; clear_api_key=true removes it."""
    c = llm_current()
    for k in ("provider", "base_url", "model"):
        if k in update and update[k] is not None:
            c[k] = str(update[k]).strip()
    if c["provider"] not in ("openai", "anthropic"):
        raise ValueError("provider must be openai (OpenAI-compatible) or anthropic")
    if c["base_url"] and not c["base_url"].lower().startswith(("http://", "https://")):
        raise ValueError("Base URL must start with http:// or https://")
    new_key = (update.get("api_key") or "").strip()
    if update.get("clear_api_key"):
        c["api_key"] = ""
    elif new_key:
        c["api_key"] = new_key
    elif c["base_url"].rstrip("/") != LLM_BASE_URL.rstrip("/") or c["provider"] != LLM_PROVIDER:
        c["api_key"] = ""  # never send a saved key to a different endpoint: pointing at a new URL needs the key typed again
    for k, lo, hi in (("max_chars", 2000, 200000), ("max_tokens", 200, 32000)):
        if update.get(k) not in (None, ""):
            try:
                v = int(update[k])
            except (TypeError, ValueError):
                raise ValueError(f"{k} must be a number")
            if not lo <= v <= hi:
                raise ValueError(f"{k} must be between {lo} and {hi}")
            c[k] = v
    if "extra_body" in update:
        raw = update["extra_body"]
        if isinstance(raw, dict):
            c["extra_body"] = raw
        elif not str(raw or "").strip():
            c["extra_body"] = {}
        else:
            try:
                v = json.loads(raw)
            except ValueError:
                raise ValueError("Extra request JSON is not valid JSON")
            if not isinstance(v, dict):
                raise ValueError("Extra request JSON must be an object, e.g. {\"reasoning\": {\"enabled\": false}}")
            c["extra_body"] = v
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = _llm_file() + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(c, f)
    os.replace(tmp, _llm_file())
    _apply_llm(c)

def load_llm_file():
    try:
        c = json.load(open(_llm_file(), encoding="utf-8"))
        if isinstance(c, dict):
            _apply_llm({**llm_current(), **c})
    except (OSError, ValueError):
        pass

load_llm_file()
