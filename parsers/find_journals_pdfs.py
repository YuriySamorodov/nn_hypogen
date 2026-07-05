#!/usr/bin/env python3
"""
Скрипт для поиска PDF-ссылок на металлургию и горнопромышленное материаловедение
на сайте https://journals.pan.pl/

Поддерживаемые журналы:
- Archives of Metallurgy and Materials (amm) - металлургия и материаловедение
- Gospodarka Surowcami Mineralnymi (gsm)     - минеральные ресурсы, горное дело
- Archives of Foundry Engineering (afe)      - литейное производство

Скрипт обходит структуру сайта:
    журнал -> год -> том -> выпуск -> статьи
и собирает все встречающиеся ссылки на PDF вида:
    https://journals.pan.pl/Content/<id>/<name>.pdf?handler=pdf

Все найденные ссылки ведут ТОЛЬКО на домен journals.pan.pl (никаких сторонних
доменов вроде imim.pl не используется и не генерируется).

Путь к выходному файлу задаётся пользователем через --output (обязательный
параметр).

Особенности сайта:
- Главная страница журнала (https://journals.pan.pl/<code>) обычно отдаётся
  сразу и содержит статьи последнего выпуска.
- Страницы конкретных лет/томов/выпусков (https://journals.pan.pl/<code>/<id>)
  иногда защищены проверкой "High Load - Verifying Browser" с автоматическим
  редиректом после паузы (похоже на защиту от перегрузки, а не классический
  Cloudflare challenge).

Стратегия обхода (в порядке приоритета):
1. Playwright (headless-браузер) — открывает страницу по-настоящему,
   выполняет JS и дожидается автоматического редиректа, как это сделал бы
   обычный браузер. Это самый надёжный способ.
   Установка: pip install playwright --break-system-packages
              playwright install chromium
2. Если Playwright недоступен или не помог — резервный способ через
   requests/cloudscraper с повторными попытками и растущими паузами между
   ними (страница сама пишет, что нужно подождать).

Ни один из способов не гарантирует 100% успеха — если сайт стабильно
отдаёт защиту, стоит увеличить паузы (--retry-wait) или запускать скрипт
с более низкой частотой запросов.
"""

import argparse
import hashlib
import logging
import re
import sys
import time
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

BASE_HOST = "https://journals.pan.pl"

# Журналы по металлургии и горному делу на journals.pan.pl
JOURNALS = {
    "amm": {
        "name": "Archives of Metallurgy and Materials",
        "base_url": f"{BASE_HOST}/amm",
        "description": "Металлургия и материаловедение",
    },
    "gsm": {
        "name": "Gospodarka Surowcami Mineralnymi",
        "base_url": f"{BASE_HOST}/gsm",
        "description": "Минеральные ресурсы, горное дело",
    },
    "afe": {
        "name": "Archives of Foundry Engineering",
        "base_url": f"{BASE_HOST}/afe",
        "description": "Литейное производство",
    },
}

JOURNAL_CODES = "|".join(JOURNALS.keys())

# Ссылки на разделы архива (год / том / выпуск) вида /amm/158695
# Строго якорится в конец строки: /amm/158695, /amm/158695/ или /amm/158695#tabs —
# но НЕ /amm/158695/publication/.../edition/.../content (это deep-link на
# отдельную статью, а не на раздел архива; такие ссылки при обходе дают только
# дублирующийся список PDF того же выпуска и не несут новой информации).
ISSUE_PATTERN = re.compile(rf"^/({JOURNAL_CODES})/(\d+)/?$")

# Реальный формат PDF-ссылок на journals.pan.pl:
# /Content/138625/AMM-2026-1-00-InMemoriam.pdf?handler=pdf
PDF_PATTERN = re.compile(r"^/Content/\d+/[^\"'?]+\.pdf(\?handler=pdf)?$")


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
    Держит один headless-браузер открытым на всё время работы скрипта,
    чтобы не тратить время на запуск браузера для каждой страницы.
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
        Открывает страницу и ждёт, пока не пропадёт экран защиты
        (страница сама делает автоматический редирект через JS/meta-refresh).
        После этого дополнительно ждёт, пока содержимое страницы не
        стабилизируется — список статей часто дозагружается через
        JS/AJAX ещё пару секунд после исчезновения экрана защиты, и если
        забрать HTML слишком рано, часть ссылок будет потеряна.
        """
        # Используем domcontentloaded вместо networkidle, т.к. на странице
        # может быть постоянная фоновый активность (AJAX), которая мешает
        # networkidle сработать. Сами дождёмся загрузки контента ниже.
        self._page.goto(url, timeout=60_000, wait_until="domcontentloaded")

        start = time.time()
        content = None
        while time.time() - start < max_wait:
            try:
                content = self._page.content()
                if not is_challenge_page(content):
                    break
            except Exception:
                # Страница ещё навигаруется, ждём немного
                pass
            
            try:
                # ждём возможный автоматический переход/обновление страницы
                self._page.wait_for_load_state("networkidle", timeout=3000)
            except Exception:
                pass
            time.sleep(2)

        if content is None or is_challenge_page(content):
            return content

        # Страница чистая — дожидаемся, пока JS-контент загрузится полностью.
        # Делаем это через ожидание появления контейнера с issues/статьями
        # и паузами, чтобы дать время AJAX-запросам завершиться.
        try:
            # Пробуем дождаться появления типичных элементов на странице
            # (на главной это список годов/томов, на странице выпуска - список статей)
            self._page.wait_for_selector("h1, h2, .issue, .volume, .article", 
                                         timeout=10000)
        except Exception:
            pass

        # Дополнительная пауза для завершения AJAX-загрузки
        time.sleep(3)

        # Проверяем, стабилизировался ли контент (число ссылок перестало расти)
        prev_link_count = -1
        for _ in range(5):
            try:
                link_count = self._page.eval_on_selector_all("a[href]", "els => els.length")
            except Exception:
                break
            if link_count == prev_link_count:
                break
            prev_link_count = link_count
            time.sleep(1)

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
    Резервная (не гарантированная) попытка обойти анти-бот challenge
    без cloudscraper. Используется только если cloudscraper не установлен.
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
            "captcha_pow", f"{ts}_{nonce}", domain="journals.pan.pl", path="/"
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
    Загружает страницу. Сначала пробует Playwright (если доступен), затем,
    при неудаче, переходит на requests/cloudscraper с растущими паузами.
    """
    # 1) Playwright — самый надёжный способ дождаться реального редиректа
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

    # 2) Резервный способ: requests/cloudscraper с повторами и паузами
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


def find_pdf_links_on_page(html: str, page_url: str) -> list[str]:
    """Находит все PDF-ссылки на странице (только на journals.pan.pl)."""
    soup = BeautifulSoup(html, "html.parser")
    pdf_links = []
    seen = set()

    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"]
        full_url = urljoin(page_url, href)

        # Оставляем только ссылки на journals.pan.pl
        if not full_url.startswith(BASE_HOST):
            continue

        path = full_url[len(BASE_HOST):]
        if PDF_PATTERN.match(path) and full_url not in seen:
            seen.add(full_url)
            pdf_links.append(full_url)

    return pdf_links


def normalize_url(url: str) -> str:
    """Нормализует URL для сравнения: убирает trailing slash и query/hash."""
    url = url.split("#")[0].split("?")[0]
    if url.endswith("/") and len(url) > len(BASE_HOST) + 1:
        url = url[:-1]
    return url


def find_section_links(html: str, journal_code: str, page_url: str) -> list[str]:
    """
    Находит ссылки на разделы архива (год/том/выпуск) для указанного журнала.
    Извлекает только канонический путь вида /<journal>/<id>, отбрасывая любые
    вложенные сегменты (например /afe/157964/publication/.../edition/.../content
    сворачивается в /afe/157964) — иначе краулер уходит на бесполезные
    вложенные страницы отдельных статей и тратит на них время.
    """
    soup = BeautifulSoup(html, "html.parser")
    links = []
    seen = set()
    page_clean = normalize_url(page_url)

    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"]
        full_url = urljoin(page_url, href)

        if not full_url.startswith(BASE_HOST):
            continue

        # Нормализуем URL перед проверкой
        clean_url = normalize_url(full_url)
        
        m = ISSUE_PATTERN.match(clean_url[len(BASE_HOST):])
        if m:
            canonical_url = f"{BASE_HOST}/{m.group(1)}/{m.group(2)}"
            if canonical_url not in seen and canonical_url != page_clean:
                seen.add(canonical_url)
                links.append(canonical_url)

    return links


class LinkWriter:
    """
    Пишет найденные PDF-ссылки в файл сразу по мере обнаружения (с flush),
    чтобы при долгом прогоне или прерывании (Ctrl+C) уже найденные ссылки
    не терялись. НЕ отслеживает и НЕ пропускает повторы — пишет в файл
    каждую найденную ссылку как есть, даже если одна и та же ссылка
    встретилась на нескольких страницах.
    """

    def __init__(self, output_path: Path, resume: bool = False):
        self.output_path = output_path
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        self.total_written = 0

        if resume and self.output_path.exists():
            # считаем, сколько строк уже есть в файле, просто для статистики
            with open(self.output_path, "r", encoding="utf-8") as f:
                self.total_written = sum(1 for line in f if line.strip())
            log.info("Resume: в файле уже %d строк, дописываем дальше", self.total_written)
            self._fh = open(self.output_path, "a", encoding="utf-8")
        else:
            # обычный запуск — начинаем файл с чистого листа
            self._fh = open(self.output_path, "w", encoding="utf-8")

    def add(self, links: list[str]) -> int:
        """Записывает все переданные ссылки в файл без каких-либо проверок на повтор."""
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


def crawl_journal(
    session,
    journal_code: str,
    max_sections: Optional[int] = None,
    playwright_fetcher: Optional[PlaywrightFetcher] = None,
    retry_wait: int = 10,
    page_delay: float = 1.5,
    writer: Optional[LinkWriter] = None,
) -> list[str]:
    """
    Обходит журнал: главная страница -> разделы архива (год/том/выпуск),
    рекурсивно собирая PDF-ссылки. Глубина обхода ограничена депами,
    встречающимися в реальной структуре сайта (обычно 2-3 уровня).
    Страницы, которые не удалось загрузить с первого раза, откладываются
    и повторно пробуются в конце (после общей паузы) — часто отваливаются
    из-за временного rate-limit, а не постоянной блокировки.
    """
    journal = JOURNALS[journal_code]
    base_url = journal["base_url"]

    log.info("Обработка журнала: %s (%s)", journal["name"], base_url)

    all_pdf_links: list[str] = []
    visited_pages: set[str] = set()
    to_visit: list[str] = [base_url]
    failed_urls: list[str] = []

    visited_count = 0
    while to_visit:
        url = normalize_url(to_visit.pop(0))
        if url in visited_pages:
            continue
        visited_pages.add(url)
        visited_count += 1

        if max_sections and visited_count > max_sections:
            log.info("Достигнут лимит max_sections=%d, останавливаемся", max_sections)
            break

        log.info("[%s] Загрузка страницы: %s", journal_code, url)
        html = fetch_page(session, url, playwright_fetcher=playwright_fetcher, retry_wait=retry_wait)
        if not html:
            log.warning("Не удалось загрузить: %s (отложено на повтор)", url)
            failed_urls.append(url)
            time.sleep(page_delay)
            continue

        pdfs = find_pdf_links_on_page(html, url)
        if pdfs:
            log.info("  -> найдено PDF: %d", len(pdfs))
        all_pdf_links.extend(pdfs)
        if writer is not None and pdfs:
            writer.add(pdfs)
            log.info("     сохранено ссылок в файл: %d", len(pdfs))

        # Ищем дальнейшие разделы (год -> том -> выпуск) только с главной
        # страницы и со страниц-разделов, чтобы не зациклиться
        sections = find_section_links(html, journal_code, url)
        for s in sections:
            if s not in visited_pages and s not in to_visit:
                to_visit.append(s)

        time.sleep(page_delay)  # вежливая пауза между запросами

    # Повторный проход по страницам, которые не удалось загрузить с первого раза
    if failed_urls:
        pause = retry_wait * 3
        log.info(
            "Повторная попытка для %d ранее неудачных страниц (после паузы %ds)...",
            len(failed_urls), pause,
        )
        time.sleep(pause)

        still_failed = []
        for url in failed_urls:
            log.info("[%s] Повтор загрузки: %s", journal_code, url)
            html = fetch_page(
                session, url,
                playwright_fetcher=playwright_fetcher,
                retries=3,
                retry_wait=retry_wait * 2,
            )
            if html:
                pdfs = find_pdf_links_on_page(html, url)
                log.info("  -> [повтор успешен] найдено PDF: %d", len(pdfs))
                all_pdf_links.extend(pdfs)
                if writer is not None and pdfs:
                    writer.add(pdfs)
                    log.info("     сохранено ссылок в файл: %d", len(pdfs))
            else:
                log.warning("  -> [повтор не удался]: %s", url)
                still_failed.append(url)
            time.sleep(page_delay)

        if still_failed:
            log.warning(
                "Не удалось загрузить %d страниц даже после повтора:\n%s",
                len(still_failed), "\n".join(still_failed),
            )

    return all_pdf_links


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Поиск PDF-ссылок на journals.pan.pl (металлургия и горное дело)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Примеры:
  %(prog)s --output links.txt                          # все журналы
  %(prog)s --journal amm --output metal_links.txt       # только металлургия
  %(prog)s --journal amm --journal afe --output links.txt
  %(prog)s --output links.txt --max-sections 20         # ограничить обход
""",
    )

    parser.add_argument(
        "--journal", "-j",
        choices=list(JOURNALS.keys()),
        action="append",
        default=None,
        help="Код журнала (amm, gsm, afe). Можно указать несколько раз.",
    )

    parser.add_argument(
        "--output", "-o",
        type=Path,
        required=True,
        help="Файл для сохранения найденных ссылок (обязательно указывается пользователем)",
    )

    parser.add_argument(
        "--max-sections",
        type=int,
        default=None,
        help="Максимум страниц разделов (год/том/выпуск) для обхода на журнал "
             "(по умолчанию: без ограничения)",
    )

    parser.add_argument(
        "--no-playwright",
        action="store_true",
        help="Не использовать Playwright, даже если он установлен "
             "(сразу перейти к резервному способу с паузами)",
    )

    parser.add_argument(
        "--retry-wait",
        type=int,
        default=10,
        help="Базовая пауза (сек) между повторными попытками в резервном "
             "способе; растёт с каждой попыткой (по умолчанию: 10)",
    )

    parser.add_argument(
        "--page-delay",
        type=float,
        default=1.5,
        help="Пауза (сек) между обычными запросами страниц, чтобы не "
             "провоцировать rate-limit сайта (по умолчанию: 1.5)",
    )

    parser.add_argument(
        "--resume",
        action="store_true",
        help="Дописывать в существующий файл вместо перезаписи, пропуская "
             "уже сохранённые ранее ссылки (удобно после прерванного запуска)",
    )

    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Подробный вывод (debug)",
    )

    args = parser.parse_args(argv)

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    selected_journals = args.journal if args.journal else list(JOURNALS.keys())
    log.info("Выбранные журналы: %s", ", ".join(selected_journals))

    session = create_session()

    output_file = args.output.resolve()
    writer = LinkWriter(output_file, resume=args.resume)
    log.info("Ссылки будут сохраняться по мере нахождения в: %s", output_file)

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
        for journal_code in selected_journals:
            crawl_journal(
                session,
                journal_code,
                args.max_sections,
                playwright_fetcher=playwright_fetcher,
                retry_wait=args.retry_wait,
                page_delay=args.page_delay,
                writer=writer,
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