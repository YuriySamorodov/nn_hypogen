from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser
from http.cookiejar import Cookie, CookieJar
from pathlib import Path
from typing import Any


BASE_URL = "https://journals.pan.pl"
DEFAULT_OUTPUT_DIR = Path("/media/andy/XS2000/data_hack/pdf_arch")
DEFAULT_QUERIES = [
    "Metallurgy",
    "Powder metallurgy",
    "Extractive metallurgy",
    "Hydrometallurgy",
    "Pyrometallurgy",
    "Mineral processing",
    "Flotation",
    "Sulfide minerals",
    "Rare earth separation",
    "Corrosion",
    "Alloys",
    "Casting",
    "Welding",
    "Heat treatment",
    "Materials science",
    "Surface engineering",
    "Coatings",
    "Tribology",
]


@dataclass(frozen=True)
class PanArticle:
    query: str
    page: int
    title: str
    article_url: str
    pdf_url: str
    content_id: str | None


class PanResultsParser(HTMLParser):
    def __init__(self, query: str, page: int) -> None:
        super().__init__(convert_charrefs=True)
        self.query = query
        self.page = page
        self.links: list[dict[str, str]] = []
        self._current_href: str | None = None
        self._current_title: str | None = None
        self._current_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        attr = {key: value or "" for key, value in attrs}
        self._current_href = attr.get("href")
        self._current_title = attr.get("title")
        self._current_text = []

    def handle_data(self, data: str) -> None:
        if self._current_href:
            self._current_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or not self._current_href:
            return
        self.links.append(
            {
                "href": urllib.parse.urljoin(BASE_URL, self._current_href),
                "title_attr": self._current_title or "",
                "text": " ".join(" ".join(self._current_text).split()),
            }
        )
        self._current_href = None
        self._current_title = None
        self._current_text = []

    def articles(self) -> list[PanArticle]:
        articles: list[PanArticle] = []
        last_article: dict[str, str] | None = None
        seen_pdf_urls: set[str] = set()
        for link in self.links:
            href = link["href"]
            if "/dlibra/publication/" in href and "/content" in href and link["text"] and link["text"] != "Go to article":
                last_article = link
                continue
            if "/Content/" not in href or "handler=pdf" not in href:
                continue
            if href in seen_pdf_urls:
                continue
            seen_pdf_urls.add(href)
            content_id = extract_content_id(href)
            title = (last_article or {}).get("text") or link["title_attr"] or Path(urllib.parse.urlparse(href).path).name
            article_url = (last_article or {}).get("href") or ""
            articles.append(
                PanArticle(
                    query=self.query,
                    page=self.page,
                    title=title,
                    article_url=article_url,
                    pdf_url=href,
                    content_id=content_id,
                )
            )
        return articles


class PanSession:
    def __init__(self, user_agent: str, timeout: int) -> None:
        self.timeout = timeout
        self.cookie_jar = CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cookie_jar))
        self.headers = {
            "User-Agent": user_agent,
            "Accept": "text/html,application/pdf;q=0.9,*/*;q=0.8",
            "Accept-Language": "en,ru;q=0.9",
        }

    def fetch_bytes(self, url: str) -> tuple[bytes, dict[str, str], str]:
        request = urllib.request.Request(url, headers=self.headers)
        with self.opener.open(request, timeout=self.timeout) as response:
            data = response.read()
            headers = {key.lower(): value for key, value in response.headers.items()}
            final_url = response.geturl()
        if is_pow_challenge(data, headers):
            self._solve_pow_cookie(data.decode("utf-8", errors="ignore"))
            request = urllib.request.Request(url, headers=self.headers)
            with self.opener.open(request, timeout=self.timeout) as response:
                data = response.read()
                headers = {key.lower(): value for key, value in response.headers.items()}
                final_url = response.geturl()
        return data, headers, final_url

    def fetch_text(self, url: str) -> str:
        data, headers, _ = self.fetch_bytes(url)
        charset = "utf-8"
        match = re.search(r"charset=([^;]+)", headers.get("content-type", ""), re.I)
        if match:
            charset = match.group(1).strip()
        return data.decode(charset, errors="ignore")

    def _solve_pow_cookie(self, html: str) -> None:
        ts_match = re.search(r'const ts = "([^"]+)"', html)
        sig_match = re.search(r'const signature = "([^"]+)"', html)
        if not ts_match or not sig_match:
            raise RuntimeError("PAN high-load page did not expose proof-of-work values")
        timestamp = ts_match.group(1)
        signature = sig_match.group(1)
        nonce = 0
        while hashlib.md5(f"{signature}{nonce}".encode("utf-8")).hexdigest().startswith("00000") is False:
            nonce += 1
        cookie = Cookie(
            0,
            "captcha_pow",
            f"{timestamp}_{nonce}",
            None,
            False,
            "journals.pan.pl",
            False,
            False,
            "/",
            True,
            False,
            None,
            False,
            None,
            None,
            {},
        )
        self.cookie_jar.set_cookie(cookie)


def main() -> int:
    parser = argparse.ArgumentParser(description="Download open PDF articles from PAS/PAN Journals dLibra search results")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--query", action="append", default=[], help="Search query; repeat for multiple queries")
    parser.add_argument("--queries-file", default=None, help="One search query per line")
    parser.add_argument("--default-queries", action="store_true", help="Use metallurgy + adjacent materials-processing queries")
    parser.add_argument("--source", choices=["sitemap", "search"], default="sitemap", help="sitemap respects robots.txt; search uses /dlibra/results")
    parser.add_argument("--allow-robots-disallowed-search", action="store_true", help="Required with --source search because robots.txt disallows /dlibra/results*")
    parser.add_argument("--start-page", type=int, default=1)
    parser.add_argument("--max-pages-per-query", type=int, default=0, help="0 means until no results")
    parser.add_argument("--max-pdfs", type=int, default=50, help="0 means no total PDF limit; requires --yes-large")
    parser.add_argument("--yes-large", action="store_true", help="Allow unbounded or very large downloads")
    parser.add_argument("--resume", action="store_true", help="Skip PDFs already present in manifest or on disk")
    parser.add_argument("--sleep", type=float, default=1.0, help="Delay between page/PDF requests")
    parser.add_argument("--timeout", type=int, default=90)
    parser.add_argument("--user-agent", default="hypothesis-factory PAN research scraper/1.0 (local archival; polite rate)")
    args = parser.parse_args()

    queries = collect_queries(args)
    if not queries:
        parser.error("set --query, --queries-file, or --default-queries")
    if args.source == "search" and not args.allow_robots_disallowed_search:
        parser.error("robots.txt disallows /dlibra/results*; use default --source sitemap or pass --allow-robots-disallowed-search explicitly")
    if args.max_pdfs == 0 and not args.yes_large:
        parser.error("--max-pdfs 0 requires --yes-large")
    if len(queries) > 3 and args.max_pdfs > 500 and not args.yes_large:
        parser.error("large multi-query scrape requires --yes-large")

    output_dir = Path(args.output_dir)
    pdf_dir = output_dir / "pdf"
    output_dir.mkdir(parents=True, exist_ok=True)
    pdf_dir.mkdir(parents=True, exist_ok=True)
    manifest_jsonl = output_dir / "pan_journals_pdfs.jsonl"
    manifest_csv = output_dir / "pan_journals_pdfs.csv"

    seen_urls, seen_ids = load_resume_state(manifest_jsonl) if args.resume else (set(), set())
    session = PanSession(args.user_agent, args.timeout)
    started_at = datetime.utcnow().isoformat(timespec="seconds")
    written = 0
    skipped = 0
    failed = 0

    with manifest_jsonl.open("a", encoding="utf-8") as jsonl:
        if args.source == "sitemap":
            articles_iter = iter_sitemap_articles(session, queries, args.sleep)
            for article in articles_iter:
                if args.max_pdfs and written >= args.max_pdfs:
                    break
                did_write, did_skip, did_fail = process_article(
                    session,
                    article,
                    pdf_dir,
                    jsonl,
                    seen_urls,
                    seen_ids,
                    args.resume,
                    args.sleep,
                )
                written += did_write
                skipped += did_skip
                failed += did_fail
                if (written + skipped + failed) % 25 == 0:
                    print(f"sitemap_progress seen={written + skipped + failed} written={written} skipped={skipped} failed={failed}", flush=True)
        else:
            for query in queries:
                page = args.start_page
                stale_pages = 0
                while True:
                    if args.max_pdfs and written >= args.max_pdfs:
                        break
                    if args.max_pages_per_query and page >= args.start_page + args.max_pages_per_query:
                        break
                    search_url = build_search_url(query, page)
                    try:
                        html = session.fetch_text(search_url)
                        articles = parse_results(html, query, page)
                    except Exception as exc:
                        failed += 1
                        print(f"page_failed query={query!r} page={page} error={exc!r}", file=sys.stderr, flush=True)
                        break
                    if not articles:
                        stale_pages += 1
                        if stale_pages >= 2:
                            break
                    else:
                        stale_pages = 0
                    print(f"query={query!r} page={page} articles={len(articles)} written={written} skipped={skipped}", flush=True)
                    for article in articles:
                        if args.max_pdfs and written >= args.max_pdfs:
                            break
                        did_write, did_skip, did_fail = process_article(
                            session,
                            article,
                            pdf_dir,
                            jsonl,
                            seen_urls,
                            seen_ids,
                            args.resume,
                            args.sleep,
                        )
                        written += did_write
                        skipped += did_skip
                        failed += did_fail
                    page += 1
                    if args.sleep:
                        time.sleep(args.sleep)
                if args.max_pdfs and written >= args.max_pdfs:
                    break

    write_csv_manifest(manifest_jsonl, manifest_csv)
    summary = {
        "started_at": started_at,
        "finished_at": datetime.utcnow().isoformat(timespec="seconds"),
        "queries": queries,
        "source": args.source,
        "output_dir": str(output_dir),
        "pdf_dir": str(pdf_dir),
        "manifest_jsonl": str(manifest_jsonl),
        "manifest_csv": str(manifest_csv),
        "downloaded": written,
        "skipped": skipped,
        "failed": failed,
    }
    (output_dir / "manifest.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return 0


def process_article(
    session: PanSession,
    article: PanArticle,
    pdf_dir: Path,
    jsonl: Any,
    seen_urls: set[str],
    seen_ids: set[str],
    resume: bool,
    sleep_seconds: float,
) -> tuple[int, int, int]:
    if article.pdf_url in seen_urls or (article.content_id and article.content_id in seen_ids):
        return 0, 1, 0
    target = pdf_dir / filename_for_article(article)
    if resume and target.exists() and target.stat().st_size > 0:
        seen_urls.add(article.pdf_url)
        if article.content_id:
            seen_ids.add(article.content_id)
        return 0, 1, 0
    try:
        metadata = download_pdf(session, article, target)
    except Exception as exc:
        record = article_to_record(article, target, "failed", {"error": str(exc)})
        jsonl.write(json.dumps(record, ensure_ascii=False) + "\n")
        jsonl.flush()
        print(f"pdf_failed url={article.pdf_url} error={exc!r}", file=sys.stderr, flush=True)
        return 0, 0, 1
    record = article_to_record(article, target, "downloaded", metadata)
    jsonl.write(json.dumps(record, ensure_ascii=False) + "\n")
    jsonl.flush()
    seen_urls.add(article.pdf_url)
    if article.content_id:
        seen_ids.add(article.content_id)
    if sleep_seconds:
        time.sleep(sleep_seconds)
    return 1, 0, 0


def collect_queries(args: argparse.Namespace) -> list[str]:
    queries: list[str] = []
    if args.default_queries:
        queries.extend(DEFAULT_QUERIES)
    queries.extend(args.query or [])
    if args.queries_file:
        for line in Path(args.queries_file).read_text(encoding="utf-8").splitlines():
            item = line.strip()
            if item and not item.startswith("#"):
                queries.append(item)
    seen: set[str] = set()
    deduped: list[str] = []
    for query in queries:
        key = query.casefold()
        if key not in seen:
            deduped.append(query)
            seen.add(key)
    return deduped


def build_search_url(query: str, page: int) -> str:
    params = {
        "q": query,
        "action": "SimpleSearchAction",
        "mdirids": "",
        "type": "-6",
        "startstr": "_all",
        "p": str(page),
    }
    return f"{BASE_URL}/dlibra/results?{urllib.parse.urlencode(params)}"


def parse_results(html: str, query: str, page: int) -> list[PanArticle]:
    parser = PanResultsParser(query, page)
    parser.feed(html)
    return parser.articles()


def iter_sitemap_articles(session: PanSession, queries: list[str], sleep_seconds: float):
    sitemap_urls = load_sitemap_index(session)
    content_sitemaps = [url for url in sitemap_urls if "/sitemap_content_" in url]
    edition_sitemaps = [url for url in sitemap_urls if "/sitemap_editions_" in url]
    pdf_by_content_id = load_pdf_sitemap_map(session, content_sitemaps, sleep_seconds)
    yielded: set[str] = set()
    for sitemap_index, sitemap_url in enumerate(edition_sitemaps, 1):
        text = session.fetch_text(sitemap_url)
        locs = extract_locs(text)
        matched = 0
        for loc in locs:
            query = matching_query(loc, queries)
            if not query:
                continue
            content_id = extract_edition_id(loc)
            if not content_id or content_id in yielded:
                continue
            pdf_url = pdf_by_content_id.get(content_id)
            if not pdf_url:
                continue
            yielded.add(content_id)
            matched += 1
            yield PanArticle(
                query=query,
                page=sitemap_index,
                title=title_from_edition_url(loc),
                article_url=to_https(loc),
                pdf_url=pdf_url,
                content_id=content_id,
            )
        print(f"sitemap={Path(urllib.parse.urlparse(sitemap_url).path).name} locs={len(locs)} matched={matched}", flush=True)
        if sleep_seconds:
            time.sleep(sleep_seconds)


def load_sitemap_index(session: PanSession) -> list[str]:
    text = session.fetch_text(f"{BASE_URL}/sitemapindex.xml")
    return [to_https(url) for url in extract_locs(text)]


def load_pdf_sitemap_map(session: PanSession, sitemap_urls: list[str], sleep_seconds: float) -> dict[str, str]:
    pdf_by_content_id: dict[str, str] = {}
    for sitemap_url in sitemap_urls:
        text = session.fetch_text(sitemap_url)
        locs = extract_locs(text)
        for loc in locs:
            if not loc.lower().endswith(".pdf") and ".pdf" not in loc.lower():
                continue
            content_id = extract_content_id(loc)
            if content_id and content_id not in pdf_by_content_id:
                pdf_by_content_id[content_id] = to_https(loc)
        print(
            f"sitemap={Path(urllib.parse.urlparse(sitemap_url).path).name} "
            f"pdf_locs={len(locs)} pdf_map={len(pdf_by_content_id)}",
            flush=True,
        )
        if sleep_seconds:
            time.sleep(sleep_seconds)
    return pdf_by_content_id


def extract_locs(xml_text: str) -> list[str]:
    return re.findall(r"<loc>(.*?)</loc>", xml_text, flags=re.I | re.S)


def matching_query(url: str, queries: list[str]) -> str | None:
    haystack = normalize_search_text(urllib.parse.unquote(url))
    for query in queries:
        needle = normalize_search_text(query)
        if needle and needle in haystack:
            return query
    return None


def normalize_search_text(value: str) -> str:
    value = value.casefold()
    value = re.sub(r"[^a-zа-я0-9]+", " ", value)
    return " ".join(value.split())


def extract_edition_id(url: str) -> str | None:
    match = re.search(r"/edition/(\d+)/", url)
    return match.group(1) if match else None


def title_from_edition_url(url: str) -> str:
    path = urllib.parse.urlparse(url).path
    slug = path.rsplit("/content/", 1)[-1] if "/content/" in path else Path(path).name
    slug = urllib.parse.unquote(slug)
    slug = re.sub(r"[-_]+", " ", slug)
    return " ".join(slug.split())[:500] or "PAN Journals article"


def to_https(url: str) -> str:
    return url.replace("http://journals.pan.pl", "https://journals.pan.pl", 1)


def download_pdf(session: PanSession, article: PanArticle, target: Path) -> dict[str, Any]:
    tmp = target.with_suffix(target.suffix + ".part")
    data, headers, final_url = session.fetch_bytes(article.pdf_url)
    content_type = headers.get("content-type", "")
    if not data.startswith(b"%PDF") and "pdf" not in content_type.lower():
        raise RuntimeError(f"response is not a PDF: content_type={content_type!r} bytes={len(data)} final_url={final_url}")
    tmp.write_bytes(data)
    tmp.replace(target)
    return {
        "bytes": target.stat().st_size,
        "sha256": sha256_file(target),
        "content_type": content_type,
        "final_url": final_url,
    }


def article_to_record(article: PanArticle, target: Path, status: str, metadata: dict[str, Any]) -> dict[str, Any]:
    return {
        "source": "pan_journals",
        "query": article.query,
        "page": article.page,
        "title": article.title,
        "article_url": article.article_url,
        "pdf_url": article.pdf_url,
        "content_id": article.content_id,
        "path": str(target),
        "status": status,
        "metadata": metadata,
        "scraped_at": datetime.utcnow().isoformat(timespec="seconds"),
    }


def filename_for_article(article: PanArticle) -> str:
    parsed = urllib.parse.urlparse(article.pdf_url)
    basename = urllib.parse.unquote(Path(parsed.path).name) or f"{article.content_id or 'pan'}.pdf"
    basename = sanitize_filename(basename)
    prefix = sanitize_filename(article.content_id or stable_hash(article.pdf_url))
    if not basename.lower().endswith(".pdf"):
        basename = f"{basename}.pdf"
    return f"{prefix}_{basename}"


def sanitize_filename(value: str) -> str:
    value = re.sub(r"[^\w.\-]+", "_", value, flags=re.UNICODE)
    value = re.sub(r"_+", "_", value).strip("._")
    return value[:180] or "document"


def extract_content_id(url: str) -> str | None:
    match = re.search(r"/Content/(\d+)/", url)
    return match.group(1) if match else None


def is_pow_challenge(data: bytes, headers: dict[str, str]) -> bool:
    content_type = headers.get("content-type", "")
    if "text/html" not in content_type.lower():
        return False
    text = data[:3000].decode("utf-8", errors="ignore")
    return "captcha_pow" in text and "const signature" in text


def load_resume_state(manifest_jsonl: Path) -> tuple[set[str], set[str]]:
    seen_urls: set[str] = set()
    seen_ids: set[str] = set()
    if not manifest_jsonl.exists():
        return seen_urls, seen_ids
    for line in manifest_jsonl.read_text(encoding="utf-8", errors="ignore").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if record.get("status") != "downloaded":
            continue
        if record.get("pdf_url"):
            seen_urls.add(str(record["pdf_url"]))
        if record.get("content_id"):
            seen_ids.add(str(record["content_id"]))
    return seen_urls, seen_ids


def write_csv_manifest(jsonl_path: Path, csv_path: Path) -> None:
    fields = ["status", "content_id", "title", "query", "page", "path", "bytes", "sha256", "article_url", "pdf_url", "scraped_at"]
    with jsonl_path.open("r", encoding="utf-8", errors="ignore") as source, csv_path.open("w", encoding="utf-8", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=fields)
        writer.writeheader()
        for line in source:
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            metadata = record.get("metadata") or {}
            writer.writerow(
                {
                    "status": record.get("status"),
                    "content_id": record.get("content_id"),
                    "title": record.get("title"),
                    "query": record.get("query"),
                    "page": record.get("page"),
                    "path": record.get("path"),
                    "bytes": metadata.get("bytes"),
                    "sha256": metadata.get("sha256"),
                    "article_url": record.get("article_url"),
                    "pdf_url": record.get("pdf_url"),
                    "scraped_at": record.get("scraped_at"),
                }
            )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def stable_hash(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:12]


if __name__ == "__main__":
    raise SystemExit(main())
