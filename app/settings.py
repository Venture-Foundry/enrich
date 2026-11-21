import os

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
