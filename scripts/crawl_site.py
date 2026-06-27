"""Crawl a government site (or sitemap) into the web corpus.

Discovers pages from sitemap.xml (XML sitemap / sitemap-index), fetches each
page with BeautifulSoup extraction, and — for JS-rendered or bot-protected
sites — renders with a Playwright headless browser. HTML tables are extracted
into the same structured table pipeline as PDFs, so crawled tables are
cell-queryable. Linked PDFs are queued into the PDF pipeline.

Usage:
  python scripts/crawl_site.py <url-or-sitemap> [--max N] [--render auto|always|never]
                                                [--no-robots] [--delay S] [--save]

Examples:
  python scripts/crawl_site.py https://www.atf.gov/
  python scripts/crawl_site.py https://www.atf.gov/sitemap.xml --max 200 --render auto
  python scripts/crawl_site.py https://example.gov/ --render always   # full client-side site

Playwright (optional, for --render auto/always to actually render JS pages):
  pip install playwright && playwright install chromium
"""
import argparse
import os
import sys


def main() -> int:
    ap = argparse.ArgumentParser(description="Crawl a site/sitemap into the web corpus")
    ap.add_argument("url", help="site root or sitemap.xml URL")
    ap.add_argument("--max", type=int, default=None, help="max pages (default: config)")
    ap.add_argument("--render", choices=["auto", "always", "never"], default=None,
                    help="headless-browser rendering mode (default: config)")
    ap.add_argument("--delay", type=float, default=None, help="polite delay between requests (s)")
    ap.add_argument("--no-robots", action="store_true", help="ignore robots.txt")
    ap.add_argument("--corpus", default=None, help="target corpus (default: web)")
    ap.add_argument("--save", action="store_true", help="commit + save updated seed after crawl")
    args = ap.parse_args()

    os.environ.setdefault("ATF_PROFILE", "local")
    from atf_graphrag.engine import Engine
    from atf_graphrag.indexing.indexer import Indexer
    from atf_graphrag.ingestion import crawler as C
    from atf_graphrag.ingestion import browser as B

    eng = Engine()
    idx = Indexer(eng, use_llm_extraction=False)

    overrides = {}
    if args.max is not None:
        overrides["max_pages"] = args.max
    if args.render is not None:
        overrides["render"] = args.render
    if args.delay is not None:
        overrides["crawl_delay"] = args.delay
    if args.no_robots:
        overrides["respect_robots"] = False
    if args.corpus:
        overrides["corpus"] = args.corpus

    render = overrides.get("render", (eng.settings.get("web") or {}).get("render", "auto"))
    if render in ("auto", "always") and not B.playwright_available():
        print("[crawl_site] NOTE: Playwright not installed — JS/bot-protected pages "
              "will fall back to static fetch. To enable rendering:\n"
              "  pip install playwright && playwright install chromium", file=sys.stderr)

    print(f"[crawl_site] crawling {args.url}  (render={render})")
    out = C.crawl_and_ingest(eng, idx, args.url, **overrides)

    pages = {k: v for k, v in out.items() if not k.startswith("pdf:")}
    pdfs = {k: v for k, v in out.items() if k.startswith("pdf:")}
    chunks = sum(out.values())
    print(f"[crawl_site] indexed {len(pages)} pages + {len(pdfs)} linked PDFs "
          f"= {chunks} chunks into corpus '{overrides.get('corpus', 'web')}'")
    for k, v in list(pages.items())[:20]:
        print(f"   {v:>4} chunks  {k}")
    if len(pages) > 20:
        print(f"   … {len(pages) - 20} more")

    if args.save:
        eng.commit()
        try:
            from atf_graphrag.indexing.table_store import get_store
            get_store(eng).build(eng)        # fold crawled tables into the store
        except Exception as ex:              # noqa: BLE001
            print(f"[crawl_site] table-store rebuild skipped: {ex}")
        from atf_graphrag.api.seeds import save_seed
        root = os.path.dirname(eng.settings["vector_store"]["path"])
        info = save_seed(root, "new", {"note": f"web crawl: {args.url}"})
        print(f"[crawl_site] committed + saved seed ({round(info['bytes']/1048576)} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
