import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from app.steps import textfit
ok = fail = 0
def check(n, c):
    global ok, fail
    if c: ok += 1
    else: fail += 1; print("FAIL", n)

menu = "[Home](https://x.ae/) [About](https://x.ae/about)\n- [Careers](https://x.ae/careers)\nAccept all cookies"
home = "## home (https://x.ae/)\n" + menu + "\n# Acme Group\nWe run 40 branches across the UAE.\n![logo](https://x.ae/l.png)\n"
svc = "## products (https://x.ae/services)\n" + menu + "\nOur services: valet parking, car wash, traffic marshals.\n" + "Detail line number %d about services.\n" * 1 % 1 + "\n".join(f"Service detail {i} with plenty of words to fill space." for i in range(200))
news = "## news (https://x.ae/news)\n" + menu + "\nLaunched a waterless car wash in 2025.\nFootnote [see more](https://x.ae/n/1)\n"
md = home + "\n" + svc + "\n" + news

pages = textfit.split_pages(md)
check("splits three pages in order", [h.split()[1] for h, _ in pages] == ["home", "products", "news"])
out = textfit.fit_text(md, 100000)
check("link URLs removed, words kept", "https://" not in out.split("\n", 1)[1].replace("(https://x.ae/services)", "").replace("(https://x.ae/news)", "") and "Careers" in out and "see more" in out)
check("menu repeated on later pages is dropped", out.count("Accept all cookies") == 1 and out.count("Careers") == 1)
check("images removed", "logo" not in out and "l.png" not in out)
small = textfit.fit_text(md, 900)
check("within the limit", len(small) <= 900)
check("long page cannot push later pages out", "waterless car wash" in small and "40 branches" in small and "valet parking" in small)
check("later short page is kept whole", "Launched a waterless car wash in 2025." in small)
check("empty input", textfit.fit_text("", 1000) == "" and textfit.fit_text("   ", 1000) == "")
nohead = textfit.fit_text("plain text only, no headers at all", 100)
check("text without page headers still works", nohead.startswith("plain text"))
check("in-page ## headings do not split pages", len(textfit.split_pages("## home (https://a.ae/)\n## Our Story\nhello\n## products (https://a.ae/p)\nx")) == 2)
print(f"{ok} passed, {fail} failed"); sys.exit(1 if fail else 0)
