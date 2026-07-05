#!/usr/bin/env python3
"""
Скрипт для поиска PDF-файлов на сайте https://rusneb.ru

Принимает поисковый запрос через параметр --query и собирает ссылки на PDF
файлы из результатов поиска.

Пример:
    python search_rusneb_pdfs.py --query "металлургия" --output pdfs.txt
    python search_rusneb_pdfs.py --query "горное дело" --output pdfs.txt --pages 5

Особенности:
- Поддерживает пагинацию (--pages)
- Использует cloudscraper/Playwright для обхода анти-бот защиты
- Сохраняет ссылки сразу по мере нахождения
"""

import argparse
import hashlib
import logging
import re
import sys
import time
import urllib.parse
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

try:
    import cloudscraper  # type: ignore
    HAVE_CLOUDSCRAPER = True
except ImportError:
    HAVE_CLOUDSCRAPER = False

try:
    from playwright.sync_api import sync_playwright  # type: ignore
    HAVE_PLAYWRIGHT = True
except ImportError:
    HAVE_PLAYWRIGHT = False

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

BASE_HOST = "https://rusneb.ru"
SEARCH_URL = f"{BASE_HOST}/search/"


def create_session():
    """Создаёт сессию (cloudscraper, если доступен, иначе обычный requests)."""
    if HAVE_CLOUDSCRAPER:
        log.info("Используется cloudscraper для обхода анти-бот защиты")
        return cloudscraper.create_scraper(
            browser={"browser": "chrome", "platform": "windows", "mobile": False}
        )
    log.info(
        "cloudscraper не установлен, используется requests + резервный обход "
        "PoW-challenge (может работать нестабильно). "
        "Рекомендуется: pip install cloudscraper --break-system-packages"
    )
    s = requests.Session()
    s.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
    })
    return s


def is_challenge_page(html: str) -> bool:
    """Проверяет, является ли страница защитным экраном ожидания."""
    return "High Load" in html and "Verifying Browser" in html


class PlaywrightFetcher:
    """
    Держит один headless-браузер открытым на всё время работы скрипта.
    """

    def __init__(self):
        self._pw = None
        self._browser = None
        self._page = None

    def start(self):
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=True)
        self._page = self._browser.new_page(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
        )

    def fetch(self, url: str, max_wait: int = 30) -> Optional[str]:
        """
        Открывает страницу и ждёт, пока не пропадёт экран защиты.
        """
        self._page.goto(url, timeout=60_000, wait_until="domcontentloaded")

        start = time.time()
        content = None
        while time.time() - start < max_wait:
            try:
                content = self._page.content()
                if not is_challenge_page(content):
                    break
            except Exception:
                pass

            try:
                self._page.wait_for_load_state("networkidle", timeout=3000)
            except Exception:
                pass
            time.sleep(2)

        if content is None or is_challenge_page(content):
            return content

        # Дожидаемся загрузки контента
        try:
            self._page.wait_for_selector(
                ".search-result, .result-item, article, .document",
                timeout=10000
            )
        except Exception:
            pass

        time.sleep(3)
        return self._page.content()

    def close(self):
        try:
            if self._browser:
                self._browser.close()
            if self._pw:
                self._pw.stop()
        except Exception:
            pass


def solve_pow_challenge_fallback(session: requests.Session, url: str) -> bool:
    """
    Резервная попытка обойти анти-бот challenge.
    """
    try:
        resp = session.get(url, timeout=30)
        html = resp.text

        ts_match = re.search(r'const ts = "(\d+)"', html)
        sig_match = re.search(r'const signature = "([a-f0-9]+)"', html)

        if not ts_match or not sig_match:
            log.warning("Не найден ожидаемый формат challenge на странице")
            return False

        ts = ts_match.group(1)
        signature = sig_match.group(1)

        log.info("Пробуем решить анти-бот challenge (резервный метод)...")
        t0 = time.time()
        nonce = 0
        max_attempts = 5_000_000
        while nonce < max_attempts:
            data = signature + str(nonce)
            h = hashlib.md5(data.encode()).hexdigest()
            if h.startswith("00000"):
                elapsed = time.time() - t0
                log.debug("nonce=%d, hash=%s (%.2fs)", nonce, h, elapsed)
                break
            nonce += 1
        else:
            log.warning("Не удалось подобрать nonce за разумное время")
            return False

        session.cookies.set(
            "captcha_pow", f"{ts}_{nonce}", domain="rusneb.ru", path="/"
        )
        return True

    except Exception as e:
        log.warning("Ошибка при решении challenge: %s", e)
        return False


def fetch_page(
    session,
    url: str,
    playwright_fetcher: Optional["PlaywrightFetcher"] = None,
    retries: int = 4,
    retry_wait: int = 10,
) -> Optional[str]:
    """
    Загружает страницу. Сначала пробует Playwright (если доступен), затем
    requests/cloudscraper с повторами и паузами.
    """
    if playwright_fetcher is not None:
        try:
            html = playwright_fetcher.fetch(url)
            if html and not is_challenge_page(html):
                return html
            log.warning(
                "Playwright: страница %s всё ещё под защитой, "
                "пробуем резервный способ (паузы + requests)",
                url,
            )
        except Exception as e:
            log.warning("Playwright не смог загрузить %s: %s. Пробуем резервный способ", url, e)

    for attempt in range(1, retries + 1):
        try:
            resp = session.get(url, timeout=60)
            resp.raise_for_status()

            if is_challenge_page(resp.text):
                if not HAVE_CLOUDSCRAPER:
                    solve_pow_challenge_fallback(session, url)
                    resp = session.get(url, timeout=60)
                    resp.raise_for_status()
                    if not is_challenge_page(resp.text):
                        return resp.text

                wait = retry_wait * attempt
                log.info(
                    "Попытка %d/%d: страница %s всё ещё под защитой, ждём %ds...",
                    attempt, retries, url, wait,
                )
                time.sleep(wait)
                continue

            return resp.text
        except Exception as e:
            log.warning("Попытка %d/%d: ошибка при загрузке %s: %s", attempt, retries, url, e)
            if attempt < retries:
                time.sleep(retry_wait)
    return None


def find_catalog_links(html: str, page_url: str) -> list[str]:
    """Находит ссылки на карточки каталога (/catalog/...) на странице поиска.
    
    Использует несколько методов для максимального покрытия:
    1. BeautifulSoup для нормального парсинга
    2. Регулярные выражения как fallback
    3. Поиск по onclick и data-атрибутам
    """
    catalog_links = []
    seen = set()

    # Метод 1: BeautifulSoup - ищем все <a> теги
    soup = BeautifulSoup(html, "html.parser")
    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"]
        if not href or href.startswith(("javascript:", "mailto:", "#")):
            continue
        
        # Обрабатываем разные формы относительных ссылок
        if href.startswith("/"):
            full_url = f"{BASE_HOST}{href}"
        elif href.startswith("http"):
            full_url = href
        else:
            full_url = urljoin(page_url, href)
        
        if not full_url.startswith(BASE_HOST):
            continue
        
        parsed = urllib.parse.urlparse(full_url)
        path = parsed.path
        m = re.match(r'^/catalog/([^/\s]+)', path)
        if m:
            normalized = f"{BASE_HOST}/catalog/{m.group(1)}"
            if normalized not in seen:
                seen.add(normalized)
                catalog_links.append(normalized)

    # Метод 2: Regex fallback - ищем href с catalog
    if not catalog_links:
        for m in re.finditer(r'href\s*=\s*["\']([^"\'>]*catalog[^"\'>]*)["\']', html, re.IGNORECASE):
            href = m.group(1)
            # Обрабатываем относительные и абсолютные ссылки
            if href.startswith("/"):
                full_url = f"{BASE_HOST}{href}"
            elif href.startswith("http"):
                full_url = href
            else:
                full_url = urljoin(page_url, href)
            
            if full_url.startswith(BASE_HOST):
                path = urllib.parse.urlparse(full_url).path
                parts = path.split("/")
                if len(parts) >= 3 and parts[1] == "catalog":
                    catalog_id = parts[2]
                    normalized = f"{BASE_HOST}/catalog/{catalog_id}"
                    if normalized not in seen:
                        seen.add(normalized)
                        catalog_links.append(normalized)

    # Метод 3: Ищем в onclick и data-атрибутах
    if not catalog_links:
        # Ищем onclick="location.href='...'" или подобные
        for m in re.finditer(r'(?:onclick|data-url|data-href)\s*=\s*["\']([^"\'>]*catalog[^"\'>]*)["\']', html, re.IGNORECASE):
            href = m.group(1)
            if href.startswith("/"):
                full_url = f"{BASE_HOST}{href}"
            elif href.startswith("http"):
                full_url = href
            else:
                full_url = urljoin(page_url, href)
            
            if full_url.startswith(BASE_HOST):
                path = urllib.parse.urlparse(full_url).path
                parts = path.split("/")
                if len(parts) >= 3 and parts[1] == "catalog":
                    catalog_id = parts[2]
                    normalized = f"{BASE_HOST}/catalog/{catalog_id}"
                    if normalized not in seen:
                        seen.add(normalized)
                        catalog_links.append(normalized)

    # Метод 4: Очень широкий поиск - любой URL с /catalog/ в тексте HTML
    if not catalog_links:
        for m in re.finditer(r'(https?://[^\s"<>]+/catalog/[^\s"<>]+)', html, re.IGNORECASE):
            url = m.group(1)
            # Очищаем URL от trailing символов
            url = url.rstrip('.,;:\'"')
            path = urllib.parse.urlparse(url).path
            parts = path.split("/")
            if len(parts) >= 3 and parts[1] == "catalog":
                catalog_id = parts[2]
                normalized = f"{BASE_HOST}/catalog/{catalog_id}"
                if normalized not in seen:
                    seen.add(normalized)
                    catalog_links.append(normalized)

    return catalog_links


GETFILES_PATTERN = re.compile(
    r'^https://rusneb\.ru/local/tools/exalead/getFiles\.php\?'
    r'[^"\'<>\s]*doc_type=pdf',
    re.IGNORECASE,
)

# Для целевых страниц каталога иногда ссылка строится по book_id из URL,
# поэтому добавляем точечный fallback по book_id.
BOOK_ID_PATTERN = re.compile(r'/catalog/([^/\s]+)')


def _normalize_url(page_url: str, href: str) -> str:
    if href.startswith("/"):
        return f"{BASE_HOST}{href}"
    if href.startswith("http"):
        return href
    return urljoin(page_url, href)


def normalize_pdf_url(url: str) -> str:
    """
    Нормализует PDF URL для дедупликации: отбрасывает параметр name,
    так как он не влияет на содержимое файла.
    """
    try:
        parsed = urllib.parse.urlparse(url)
        params = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        filtered = [(k, v) for k, v in params if k != "name"]
        new_query = urllib.parse.urlencode(filtered)
        return urllib.parse.urlunparse(parsed._replace(query=new_query))
    except Exception:
        return url


def deduplicate_file(filepath: Path) -> int:
    """
    Удаляет дубликаты из файла, оставляя первое вхождение каждого URL.
    Возвращает количество удалённых дубликатов.
    """
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            lines = [line.rstrip("\n") for line in f if line.strip()]
        
        seen = set()
        unique_lines = []
        for line in lines:
            normalized = normalize_pdf_url(line)
            if normalized not in seen:
                seen.add(normalized)
                unique_lines.append(line)
        
        removed = len(lines) - len(unique_lines)
        if removed > 0:
            with open(filepath, "w", encoding="utf-8") as f:
                for line in unique_lines:
                    f.write(line + "\n")
            log.info("Удалено дубликатов из %s: %d", filepath, removed)
        return removed
    except Exception as e:
        log.warning("Не удалось очистить дубликаты в %s: %s", filepath, e)
        return 0


def find_pdf_links_on_page(html: str, page_url: str, exclude_pattern: Optional[re.Pattern] = None) -> list[str]:
    """Находит только ссылки вида /local/tools/exalead/getFiles.php?...&doc_type=pdf.
    
    Args:
        exclude_pattern: Regex pattern для исключения URL (например, для *_bibl_*)
    """
    candidates: list[str] = []
    seen_hrefs: set[str] = set()

    def collect(href: str) -> None:
        if not href or href in seen_hrefs:
            return
        seen_hrefs.add(href)
        candidates.append(href)

    # Метод 1: BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    for a_tag in soup.find_all("a", href=True):
        collect(a_tag["href"])

    # Метод 2: Regex fallback
    if not candidates:
        for m in re.finditer(r'href\s*=\s*["\']([^"\'>]*)["\']', html, re.IGNORECASE):
            collect(m.group(1))

    # Метод 3: Очень широкий fallback - ищем любые URL с getFiles.php и doc_type=pdf в тексте
    if not candidates:
        for m in re.finditer(r'(https?://[^\s"<>]+getFiles\.php[^\s"<>]*doc_type=pdf)', html, re.IGNORECASE):
            collect(m.group(1))
        for m in re.finditer(r'(/local/tools/exalead/getFiles\.php[^\s"<>]*doc_type=pdf)', html, re.IGNORECASE):
            collect(m.group(1))

    # Метод 4: Поиск по js-вызовам и JSON в HTML
    if not candidates:
        for m in re.finditer(r'(https?://[^\s"\'\)]+getFiles\.php[^\s"\'\)]*doc_type=pdf)', html, re.IGNORECASE):
            collect(m.group(1))

    # Метод 5: Точечный fallback по book_id из URL страницы
    if not candidates:
        m = BOOK_ID_PATTERN.search(page_url)
        if m:
            book_id = m.group(1)
            # Ищем любую подстроку вида getFiles.php?book_id=<book_id>...&doc_type=pdf
            pattern = re.compile(
                r'https?://[^\s"<>]+getFiles\.php\?[^"<>]*book_id=' + re.escape(book_id) + r'[^"<>]*doc_type=pdf',
                re.IGNORECASE,
            )
            for m2 in pattern.finditer(html):
                collect(m2.group(0))

    # Нормализуем и фильтруем только getFiles.php с doc_type=pdf
    pdf_links: list[str] = []
    seen: set[str] = set()
    matched = []
    unmatched = []
    excluded = []
    for href in candidates:
        full_url = _normalize_url(page_url, href)
        if not full_url.startswith(BASE_HOST):
            continue
        
        # Проверяем исключение по паттерну
        if exclude_pattern and exclude_pattern.search(full_url):
            excluded.append(full_url)
            continue
        
        path = urllib.parse.urlparse(full_url).path
        if "getFiles.php" in path or "getFiles.php" in full_url:
            if "doc_type=pdf" in full_url:
                normalized = normalize_pdf_url(full_url)
                if normalized not in seen:
                    seen.add(normalized)
                    pdf_links.append(normalized)
                    matched.append(full_url)
            else:
                unmatched.append(full_url)

    # Диагностика
    if log.isEnabledFor(logging.DEBUG):
        log.debug("find_pdf_links_on_page: рассмотрено href=%d, совпало=%d, не_совпало=%d, исключено=%d",
                  len(candidates), len(matched), len(unmatched), len(excluded))
        if unmatched:
            log.debug("  примеры без doc_type=pdf: %s", unmatched[:3])
        if excluded:
            log.debug("  исключены по паттерну: %s", excluded[:3])

    return pdf_links


def find_next_page_link(html: str, page_url: str) -> Optional[str]:
    """Ищет ссылку на следующую страницу пагинации."""
    soup = BeautifulSoup(html, "html.parser")
    current_page = 1
    # Поддерживаем разные параметры пагинации: page, PAGEN_1, PAGEN_2 и т.д.
    page_match = re.search(r'[?&](?:page|PAGEN_\d+)=(\d+)', page_url)
    if page_match:
        current_page = int(page_match.group(1))
    next_url = None
    next_page = None
    
    # Сбор всех кандидатов для диагностики
    candidates = []
    
    # Метод 1: Ищем по параметрам пагинации (page, PAGEN_1, PAGEN_2 и т.д.)
    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"]
        full_url = urljoin(page_url, href)
        if not full_url.startswith(BASE_HOST):
            continue
        # Пропускаем ссылки на каталог
        path = full_url[len(BASE_HOST):]
        if re.match(r'^/catalog/', path):
            continue
        # Ищем разные параметры пагинации
        pm = re.search(r'[?&](?:page|PAGEN_\d+)=(\d+)', full_url)
        if pm:
            num = int(pm.group(1))
            param_name = pm.group(0).split('=')[0].lstrip('?&')
            candidates.append(f"{full_url} ({param_name}={num})")
            if num > current_page:
                if next_page is None or num < next_page:
                    next_page = num
                    next_url = full_url
    
    # Метод 2: Ищем по классам пагинации (включая старые классы Kendo UI)
    if not next_url:
        pagination_keywords = ["next", "pagination-next", "forward", "pager-next", "btn-next"]
        for a_tag in soup.find_all("a", href=True):
            href = a_tag["href"]
            full_url = urljoin(page_url, href)
            if not full_url.startswith(BASE_HOST):
                continue
            path = full_url[len(BASE_HOST):]
            if re.match(r'^/catalog/', path):
                continue
            
            classes = " ".join(a_tag.get("class", []))
            if any(kw in classes.lower() for kw in pagination_keywords):
                candidates.append(f"{full_url} (class={classes})")
                next_url = full_url
                break
    
    # Метод 3: Ищем ссылку с текстом "следующая", "next", ">", "»"
    if not next_url:
        pagination_texts = ["следующая", "next", ">", "»", "далее"]
        for a_tag in soup.find_all("a", href=True):
            href = a_tag["href"]
            full_url = urljoin(page_url, href)
            if not full_url.startswith(BASE_HOST):
                continue
            path = full_url[len(BASE_HOST):]
            if re.match(r'^/catalog/', path):
                continue
            
            text = a_tag.get_text(strip=True).lower()
            if any(pt in text for pt in pagination_texts):
                candidates.append(f"{full_url} (text='{text}')")
                next_url = full_url
                break
    
    # Метод 4: Ищем по rel="next"
    if not next_url:
        for a_tag in soup.find_all("a", attrs={"rel": "next"}):
            href = a_tag.get("href")
            if href:
                full_url = urljoin(page_url, href)
                if full_url.startswith(BASE_HOST):
                    candidates.append(f"{full_url} (rel=next)")
                    next_url = full_url
                    break
    
    # Метод 5: Ищем по data-page атрибуту (Kendo UI style)
    if not next_url:
        for a_tag in soup.find_all(attrs={"data-page": True}):
            try:
                num = int(a_tag.get("data-page", 0))
                if num > current_page:
                    href = a_tag.get("href", "")
                    if href:
                        full_url = urljoin(page_url, href)
                        if full_url.startswith(BASE_HOST):
                            candidates.append(f"{full_url} (data-page={num})")
                            if next_page is None or num < next_page:
                                next_page = num
                                next_url = full_url
            except (ValueError, TypeError):
                pass
    
    # Метод 6: Ищем ul/li пагинацию ( Bootstreр, Kendo и т.д.)
    if not next_url:
        pagination_containers = soup.find_all("ul", class_=lambda c: c and "pagination" in " ".join(c).lower())
        if not pagination_containers:
            pagination_containers = soup.find_all("div", class_=lambda c: c and "pagination" in " ".join(c).lower())
        
        for container in pagination_containers:
            for a_tag in container.find_all("a", href=True):
                href = a_tag["href"]
                full_url = urljoin(page_url, href)
                if not full_url.startswith(BASE_HOST):
                    continue
                path = full_url[len(BASE_HOST):]
                if re.match(r'^/catalog/', path):
                    continue
                pm = re.search(r'[?&]page=(\d+)', full_url)
                if pm:
                    num = int(pm.group(1))
                    candidates.append(f"{full_url} (pagination page={num})")
                    if num > current_page:
                        if next_page is None or num < next_page:
                            next_page = num
                            next_url = full_url
    
    # Метод 7: Ищем по aria-label="Next" или aria-label="Следующая"
    if not next_url:
        for a_tag in soup.find_all("a", attrs={"aria-label": True}):
            aria_label = a_tag.get("aria-label", "").lower()
            if "next" in aria_label or "след" in aria_label:
                href = a_tag.get("href", "")
                if href:
                    full_url = urljoin(page_url, href)
                    if full_url.startswith(BASE_HOST):
                        candidates.append(f"{full_url} (aria-label='{aria_label}')")
                        next_url = full_url
                        break
    
    # Диагностика
    if log.isEnabledFor(logging.DEBUG):
        log.debug("find_next_page_link: текущая страница=%d, кандидатов=%d", current_page, len(candidates))
        if candidates:
            log.debug("  кандидаты: %s", "; ".join(candidates[:10]))
        else:
            log.debug("  кандидатов не найдено")
        if not next_url:
            log.debug("  следующая страница не определена")
    
    return next_url


class LinkWriter:
    """
    Пишет найденные PDF-ссылки в файл сразу по мере обнаружения.
    """

    def __init__(self, output_path: Path, resume: bool = False):
        self.output_path = output_path
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        self.total_written = 0

        if resume and self.output_path.exists():
            with open(self.output_path, "r", encoding="utf-8") as f:
                self.total_written = sum(1 for line in f if line.strip())
            log.info("Resume: в файле уже %d строк, дописываем дальше", self.total_written)
            self._fh = open(self.output_path, "a", encoding="utf-8")
        else:
            self._fh = open(self.output_path, "w", encoding="utf-8")

    def add(self, links: list[str]) -> int:
        for link in links:
            self._fh.write(link + "\n")
        if links:
            self._fh.flush()
            self.total_written += len(links)
        return len(links)

    def close(self):
        try:
            self._fh.close()
        except Exception:
            pass


def build_search_url(query: str, page: int = 1, categories: Optional[list[int]] = None) -> str:
    """Строит URL поиска с учётом запроса, номера страницы и фильтров категорий."""
    from urllib.parse import quote
    encoded_query = quote(query)
    params = [("q", encoded_query), ("access[]", "open")]
    
    if categories:
        for cat in categories:
            params.append(("c[]", str(cat)))
    
    if page > 1:
        # Сайт использует PAGEN_1 для пагинации
        params.append(("PAGEN_1", str(page)))
    
    query_string = "&".join(f"{k}={v}" for k, v in params)
    return f"{SEARCH_URL}?{query_string}"


def crawl_search(
    session,
    query: str,
    pages: int = 1,
    playwright_fetcher: Optional["PlaywrightFetcher"] = None,
    retry_wait: int = 10,
    page_delay: float = 1.5,
    writer: Optional[LinkWriter] = None,
    categories: Optional[list[int]] = None,
    exclude_pattern: Optional[re.Pattern] = None,
    existing_normalized_urls: Optional[set[str]] = None,
) -> list[str]:
    """
    Обходит страницы поиска и собирает PDF-ссылки.
    На странице поиска извлекает ссылки на карточки каталога (/catalog/...),
    затем обходит каждую карточку и собирает PDF-ссылку.
    Если pages=0, обходит все доступные страницы.
    Ссылки записываются в файл сразу при находке (не в конце выполнения).
    """
    log.info("Поисковый запрос: %s", query)
    if pages == 0:
        log.info("Обход ВСЕХ доступных страниц поиска")
    else:
        log.info("Обход %d страниц(ы) поиска", pages)

    if categories:
        log.info("Фильтр по категориям: %s", categories)

    if exclude_pattern:
        log.info("Исключение URL по паттерну: %s", exclude_pattern.pattern)

    all_pdf_links: list[str] = []
    page_num = 1
    visited_catalogs: set[str] = set()
    if existing_normalized_urls is None:
        existing_normalized_urls = set()
    
    while True:
        if pages != 0 and page_num > pages:
            break
            
        url = build_search_url(query, page_num, categories=categories)
        page_label = f"{page_num}" if pages == 0 else f"{page_num}/{pages}"
        log.info("Страница %s: %s", page_label, url)
        
        html = fetch_page(
            session, url,
            playwright_fetcher=playwright_fetcher,
            retry_wait=retry_wait
        )
        if not html:
            log.warning("Не удалось загрузить страницу %d", page_num)
            time.sleep(page_delay)
            break

        # Извлекаем ссылки на карточки каталога со страницы поиска
        catalog_links = find_catalog_links(html, url)
        log.info("  -> найдено карточек каталога: %d", len(catalog_links))
        if catalog_links:
            for link in catalog_links[:3]:
                log.info("     пример: %s", link)
        else:
            log.warning(
                "Карточки каталога не найдены. Проверьте HTML страницы через --verbose"
            )
            if log.isEnabledFor(logging.DEBUG):
                log.debug("HTML样品 (первые 2000 символов): %s", html[:2000])

        # Обходим каждую карточку и собираем PDF
        page_pdfs: list[str] = []
        for catalog_url in catalog_links:
            if catalog_url in visited_catalogs:
                continue
            visited_catalogs.add(catalog_url)

            # Пропускаем карточки с _bibl_ в slug
            if "_bibl_" in catalog_url:
                log.debug("     пропуск _bibl_ карточки: %s", catalog_url)
                continue

            log.info("     обход каталога: %s", catalog_url)
            catalog_html = fetch_page(
                session, catalog_url,
                playwright_fetcher=playwright_fetcher,
                retry_wait=retry_wait
            )
            if not catalog_html:
                log.warning("     не удалось загрузить карточку %s", catalog_url)
                time.sleep(page_delay)
                continue

            pdfs = find_pdf_links_on_page(catalog_html, catalog_url, exclude_pattern=exclude_pattern)
            if pdfs:
                log.info("       -> найдено PDF: %d", len(pdfs))
                for pdf_url in pdfs:
                    log.info("       -> найдена ссылка: %s", pdf_url)
            else:
                log.debug("       -> PDF не найдены на карточке: %s", catalog_url)
            page_pdfs.extend(pdfs)
            
            # Сначала добавляем в writer, потом спим
            if writer is not None and pdfs:
                # Фильтруем уже существующие URL (дедупликация по нормализованному виду)
                new_pdfs = []
                for pdf_url in pdfs:
                    normalized = normalize_pdf_url(pdf_url)
                    if normalized not in existing_normalized_urls:
                        new_pdfs.append(pdf_url)
                        existing_normalized_urls.add(normalized)
                
                if new_pdfs:
                    writer.add(new_pdfs)
                    log.info("       -> сохранено в файл: %d (новых)", len(new_pdfs))
                else:
                    log.debug("       -> все ссылки уже были в файле, пропуск записи")
            
            time.sleep(page_delay)

        if page_pdfs:
            log.info("  -> всего PDF со страницы поиска: %d", len(page_pdfs))
        all_pdf_links.extend(page_pdfs)

        # Если pages=0, проверяем, есть ли следующая страница поиска
        if pages == 0:
            next_page_url = find_next_page_link(html, url)
            if not next_page_url:
                # Fallback: пробуем следующую страницу по номеру
                next_page_num = page_num + 1
                next_url_by_num = build_search_url(query, next_page_num, categories=categories)
                log.info("Ссылка на следующую страницу не найдена, пробуем страницу %d: %s", next_page_num, next_url_by_num)
                
                # Проверяем, есть ли на следующей странице результаты
                next_html = fetch_page(session, next_url_by_num, playwright_fetcher=playwright_fetcher, retry_wait=retry_wait)
                if next_html:
                    next_catalogs = find_catalog_links(next_html, next_url_by_num)
                    if next_catalogs:
                        log.info("На следующей странице найдено карточек: %d", len(next_catalogs))
                        next_page_url = next_url_by_num
                    else:
                        log.warning("На следующей странице карточек нет, возможно это конец результатов")
                        if log.isEnabledFor(logging.DEBUG):
                            log.debug("HTML следующей страницы (первые 1000 символов): %s", next_html[:1000])
                        break
                else:
                    log.warning("Не удалось загрузить следующую страницу, завершаем обход")
                    break
            
            if not catalog_links:
                log.info("Нет карточек на странице, вероятно это конец результатов")
                break

        page_num += 1
        
        # Пауза между страницами
        if pages == 0:
            time.sleep(page_delay)

    return all_pdf_links


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Поиск PDF-файлов на rusneb.ru",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Примеры:
  %(prog)s --query "металлургия" --output metallurgy_pdfs.txt
  %(prog)s --query "горное дело" --output mining.txt --pages 5
  %(prog)s --query "engineering" --output eng.pdfs --all
  %(prog)s --query "инженерия" -o eng.pdfs --pages all --resume
  %(prog)s -q "металлургия" -o pdfs.txt --categories 3 30 31 25 4 8 --all
""",
    )

    parser.add_argument(
        "--query", "-q",
        type=str,
        required=True,
        help="Поисковый запрос (будет подставлен в параметр q=)",
    )

    parser.add_argument(
        "--output", "-o",
        type=Path,
        required=True,
       help="Файл для сохранения найденных ссылок (обязательно)",
    )

    parser.add_argument(
        "--pages", "-p",
        type=str,
        default="1",
        help="Количество страниц поиска для обхода (число или 'all' для всех доступных, по умолчанию: 1)",
    )

    parser.add_argument(
        "--all",
        action="store_true",
        help="Обойти все доступные страницы (эквивалент --pages all)",
    )

    parser.add_argument(
        "--categories", "-c",
        type=int,
        nargs="+",
        default=None,
        help="Фильтр по категориям (ID). Пример: --categories 3 30 31 25 4 8",
    )

    parser.add_argument(
        "--exclude-url",
        type=str,
        default=None,
        help="Regex паттерн для исключения URL (например, '.*_bibl_.*' для исключения ссылок с _bibl_)",
    )

    parser.add_argument(
        "--no-playwright",
        action="store_true",
        help="Не использовать Playwright, даже если он установлен",
    )

    parser.add_argument(
        "--retry-wait",
        type=int,
        default=10,
        help="Базовая пауза (сек) между повторными попытками (по умолчанию: 10)",
    )

    parser.add_argument(
        "--page-delay",
        type=float,
        default=1.5,
        help="Пауза (сек) между страницами (по умолчанию: 1.5)",
    )

    parser.add_argument(
        "--resume",
        action="store_true",
        help="Дописывать в существующий файл вместо перезаписи",
    )



    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Подробный вывод (debug)",
    )

    args = parser.parse_args(argv)

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    output_file = args.output.resolve()

    # Если файл уже существует и включен resume, пытаемся загрузить существующие URL для дедупликации
    existing_urls = set()
    if args.resume and output_file.exists():
        try:
            deduplicate_file(output_file)
            with open(output_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        existing_urls.add(normalize_pdf_url(line))
            log.info("Resume: загружено %d существующих URL для дедупликации", len(existing_urls))
        except Exception:
            pass

    # Определяем количество страниц: --all или --pages all
    if args.all:
        pages = 0
    else:
        pages_str = args.pages
        if pages_str == "all":
            pages = 0
        else:
            try:
                pages = int(pages_str)
            except ValueError:
                log.error("Неверное значение для --pages: %s. Используйте число или 'all'", pages_str)
                return 1

    exclude_pattern = None
    if args.exclude_url:
        try:
            exclude_pattern = re.compile(args.exclude_url)
        except re.error as e:
            log.error("Ошибка в регулярном выражении --exclude-url: %s", e)
            return 1

    session = create_session()

    writer = LinkWriter(output_file, resume=args.resume)
    log.info("Ссылки будут сохраняться по мере нахождения в: %s", output_file)
    
    # Синхронизируем счётчик с файлом
    if args.resume and output_file.exists():
        try:
            with open(output_file, "r", encoding="utf-8") as f:
                writer.total_written = sum(1 for line in f if line.strip())
        except Exception:
            pass

    playwright_fetcher = None
    use_playwright = HAVE_PLAYWRIGHT and not args.no_playwright
    if use_playwright:
        log.info("Playwright доступен, будет использоваться в первую очередь")
        playwright_fetcher = PlaywrightFetcher()
        try:
            playwright_fetcher.start()
        except Exception as e:
            log.warning("Не удалось запустить Playwright (%s), переключаюсь на резервный способ", e)
            playwright_fetcher = None
    else:
        if not HAVE_PLAYWRIGHT:
            log.info(
                "Playwright не установлен, будет использован только резервный способ "
                "(pip install playwright --break-system-packages && playwright install chromium)"
            )

    try:
        crawl_search(
            session,
            args.query,
            pages,
            playwright_fetcher=playwright_fetcher,
            retry_wait=args.retry_wait,
            page_delay=args.page_delay,
            writer=writer,
            categories=args.categories,
            exclude_pattern=exclude_pattern,
            existing_normalized_urls=existing_urls,
        )
    finally:
        if playwright_fetcher is not None:
            playwright_fetcher.close()
        writer.close()

    log.info("Готово. Всего строк записано в файл: %d", writer.total_written)
    log.info("Ссылки сохранены в: %s", output_file)
    return 0


if __name__ == "__main__":
    sys.exit(main())