"""Turn the stored multi-page site text into the best possible excerpt for an LLM.

The crawl stores one markdown block per page ("## <name> (<url>)" then the page text). Cutting that at N characters used to drop whole
pages from the end, and every page repeats its menu, footer and link URLs. fit_text() instead:
  1. strips link URLs and images (the words stay, the long URLs go),
  2. drops lines already seen on an earlier page (menus, footers, cookie notices),
  3. shares the character budget across all pages so each page is represented, cutting each page at a line boundary.
"""
import re

HEADER = re.compile(r"(?m)^## [\w\- ]+ \(https?://[^\n]*\)[ \t]*$")
_IMG = re.compile(r"!\[[^\]]*\]\((?:[^()\s]|\([^)]*\))*(?:\s+\"[^\"]*\")?\)")
_LINK = re.compile(r"\[([^\]]*)\]\((?:[^()\s]|\([^)]*\))*(?:\s+\"[^\"]*\")?\)")
_BARE_URL = re.compile(r"https?://\S+")


def split_pages(md: str):
    """[(header_line, body)] in page order; text before the first header (if any) becomes a headerless first page."""
    heads = list(HEADER.finditer(md or ""))
    if not heads:
        return [("", md or "")] if (md or "").strip() else []
    pages = []
    if md[:heads[0].start()].strip():
        pages.append(("", md[:heads[0].start()]))
    for i, h in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(md)
        pages.append((h.group(0).strip(), md[h.end():end]))
    return pages


def _clean_line(line: str) -> str:
    line = _IMG.sub("", line)
    line = _LINK.sub(lambda m: m.group(1).strip(), line)
    line = _BARE_URL.sub("", line)
    return re.sub(r"[ \t]+", " ", line).strip()


def tidy_pages(pages):
    """Clean every line and drop lines already used on an earlier page. Short lines (under 4 chars) are kept as they are."""
    seen, out = set(), []
    for head, body in pages:
        kept = []
        for raw in body.splitlines():
            line = _clean_line(raw)
            if not line or re.fullmatch(r"[-*_#>|\s]+", line):
                continue
            key = line.lower()
            if len(line) >= 4 and key in seen:
                continue
            seen.add(key)
            kept.append(line)
        out.append((head, "\n".join(kept)))
    return out


def _share(sizes, budget):
    """Water-filling: pages shorter than an equal share keep all of it, the unused budget goes to the longer pages."""
    n = len(sizes)
    alloc = [0] * n
    order = sorted(range(n), key=lambda i: sizes[i])
    left, remaining = budget, n
    for i in order:
        give = min(sizes[i], left // remaining if remaining else 0)
        alloc[i] = give
        left -= give
        remaining -= 1
    return alloc


def _cut(text, n):
    if len(text) <= n:
        return text
    cut = text[:n]
    nl = cut.rfind("\n")
    return cut[:nl] if nl > n * 0.6 else cut


def fit_text(md: str, limit: int) -> str:
    pages = tidy_pages(split_pages(md))
    pages = [(h, b) for h, b in pages if b.strip()]
    if not pages:
        return ""
    heads = [len(h) + 1 for h, _ in pages]
    body_budget = max(limit - sum(heads), 0)
    alloc = _share([len(b) for _, b in pages], body_budget)
    parts = []
    for (h, b), a in zip(pages, alloc):
        body = _cut(b, a)
        if body.strip():
            parts.append((h + "\n" if h else "") + body)
    return "\n\n".join(parts)[:limit]
