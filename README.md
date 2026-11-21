# Enrichment Portal

Upload a CSV of companies, pick enrichment steps, download a new CSV with extra `enr_*` columns.
Built for the Apollo UAE list (domain-keyed), crawl4ai for website crawling, no LLM required.

## Run it (next to your crawl4ai)
```bash
cp .env.example .env        # set CRAWL4AI_URL (example value is http://192.168.210.10:11235)
docker compose up -d --build
# open http://<this-host>:8080
```
On Unraid: run `docker compose` from a terminal, or add a container built from this folder.
The header pill shows whether crawl4ai is reachable; **Test crawl** does a real one-page crawl (the crawl4ai box needs internet for that).
If you expose the portal through a tunnel, set `PORTAL_USER` / `PORTAL_PASSWORD` in `.env`.

## Using it
1. Drop a CSV (any file with a domain or website column; the Apollo export works as-is).
2. Pick steps. For a test list use **first N rows** (e.g. 25).
3. Start, watch progress, download. The CSV can be downloaded at any time (partial until finished).
4. Check the output, tune, then run the full file. Cached lookups (30 days) make re-runs near-instant;
   tick **Ignore cache** to re-fetch.

## What each step adds (all columns are prefixed `enr_`)
| Step | Needs | Adds |
|---|---|---|
| DNS footprint | DNS only | mail provider (M365 / Google / gateways), M365 evidence, Teams/SIP DNS evidence (CentrePal), SPF includes, DMARC policy, DNS host, SaaS vendors revealed by TXT verification records |
| M365 tenant lookup | outbound HTTPS to login.microsoftonline.com | has tenant, managed/federated, tenant ID |
| Website crawl | crawl4ai | title, description, language, CMS/e-commerce platform, tech stack (chat, CRM, contact-centre, analytics, payments, ATS), emails, phones, WhatsApp, P.O. Box, social URLs, careers page + ATS, emirates mentioned/HQ guess, industry guess + confidence |
| ISV signal packs | the steps above | `enr_pack_<isv>_score` and `_hits` from `config/packs.json` (starter packs: centrepal, pulpoar; edit freely) |
| LLM summary (optional) | LLM_* in `.env` | one-liner, offerings, sector, B2B/B2C, HQ emirate, named people; with a focus: the full `enr_p_*` / `enr_c_*` profile (see below) |

Status per row: `ok`, `partial` (a step failed, or site unreachable/blocked), `error`, `no_domain` (row passed through untouched).

## Partner and customer focus
Pick **Profile focus** in step 2 (or `"focus": "partner" | "customer" | "both"` in the job options). It does two things:
1. **Deeper crawl** (up to 8 extra pages, default 7): partner focus reads services, solutions, partners/alliances, industries, clients/case studies and certifications; customer focus reads products, locations, news/investor and company-story pages. The crawl is cached separately (`web_partner`, `web_customer`, `web_both`) from the basic one.
2. **Structured LLM profile** (needs the LLM step on; up to `LLM_MAX_CHARS` of crawled text per company is sent):
   * `enr_p_*` partner columns: one-liner, primary focus, summary (150-250 words), partner type, service lines, technologies, vendor partnerships with tier/evidence, Microsoft/Azure signals, own products/IP, industries served, named customers, delivery model, geography, certifications, scale, channel/co-sell signals, key people.
   * `enr_c_*` customer columns: one-liner, summary, sector (always one value from a fixed 33-item list, with up to 2 secondary sectors and the evidence for the choice), sub-sector, business model, products/services, customer channels (call centre, WhatsApp, app...), locations, scale, technology signals, initiatives/news, functions signalled, key people.

The model is told to use only the crawled text and to leave a field blank rather than guess. Run a 25-row test first, read the output, then run the full list.
Token usage per job (in/out) is shown in the progress panel and saved with the job, so you can see the real cost per company.

### ISV fit signals (customer focus)
Define each ISV in `config/isvs.json`: what it does, its ideal customer, yes/no **signals** (each with a weight; negative = already served / poor fit) and named competitors.
Tick the ISVs in step 2 (shown when the LLM step is on with a customer or both focus). In the same LLM call the model answers each signal from the website text only,
quoting short evidence and listing any named competitor it finds; the portal then scores it by rule. Per ISV you get `enr_isv_<id>_score` (0-100), `_hits` (signals found, with evidence),
`_incumbents` (competitor tools named on the site) and `_notes`. "Not mentioned" is scored as not found, never as a guess. Entries with no signals are ignored.
CentrePal and PulpoAR are drafts; the other ISVs are empty stubs to fill in. Changing a definition automatically refreshes cached answers for that ISV set.

### OpenRouter and other options
`LLM_EXTRA_BODY` merges JSON into every request, e.g. for OpenRouter `{"provider":{"data_collection":"deny"},"reasoning":{"enabled":false}}`
(keeps prompts away from providers that retain data, and stops reasoning models spending output tokens on thinking).
To compare models on the same rows: run a 25-row job per model (change `LLM_MODEL`, restart). The crawl is reused from cache, and LLM answers are cached per model, so each model is really called and the comparison is fair.

LLM providers: `LLM_PROVIDER=openai` (default, any OpenAI-compatible endpoint) or `LLM_PROVIDER=anthropic` (native Claude Messages API; set `LLM_API_KEY` and `LLM_MODEL`, leave `LLM_BASE_URL` blank).

## Do you need an LLM?
Not for this first pass: DNS, tech detection, contacts, careers and a keyword-based industry guess are all rule-based.
An LLM helps with: a reliable one-line "what they do", cleaner industry labels, named leadership, and ISV-fit reasoning.
Recommended: keep it off for the full list and enable it only for shortlisted rows (the step has a max-rows cap and an
"only rows where column equals..." filter, e.g. tier = Platinum/Gold). It speaks the OpenAI chat-completions API (or native Claude with `LLM_PROVIDER=anthropic`), so OpenRouter,
Ollama or a hosted model all work. The basic summary sends about 9k characters of crawled text per company; the partner/customer profiles send up to `LLM_MAX_CHARS` (default 30k).

## Notes and limits
* Rows with no domain (450 in the Apollo file) are not enriched; they need a domain-discovery step first.
* `enr_industry_guess` and the emirate guess are keyword heuristics from website text; with a customer focus use `enr_c_sector` (LLM, fixed list, with evidence) instead.
* Crawling is modest by default (3 concurrent crawl4ai requests, up to 4 extra pages per site). Adjust with `CRAWL_CONCURRENCY`.
* DNS runs from the container. If lookups time out, set `DNS_SERVERS=1.1.1.1,8.8.8.8`.
* If your crawl4ai version rejects the config objects, the portal retries once with a bare `{"urls": [...]}` payload; you can also
  override `CRAWL4AI_BROWSER_CONFIG` / `CRAWL4AI_CRAWLER_CONFIG` (JSON) in `.env`.
* Data lives in `./data` (cache.db, jobs/<id>/input.csv, results.jsonl, enriched.csv).

## Tests
`python -m tests.run_test` runs 50+ checks against a mock crawl4ai (`pip install -r requirements.txt` first); `python -m tests.test_focus` covers the partner/customer focus.
