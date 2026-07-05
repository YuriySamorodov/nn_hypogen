#!/usr/bin/env python3
"""
Скрипт для скачивания PDF файлов с архива сайта https://www.imim.pl/archives

Структура сайта:
  - Главная страница содержит меню со ссылками на выпуски (том/номер/год).
  - Каждая страница выпуска содержит список статей со ссылками на PDF.

Структура сохранения:
  {destination}/{year}_{volume}/Volume_{issue:02d}/{number:02d}.pdf

Примеры ссылок на PDF:
  https://www.imim.pl/files/archiwum/Vol1_2026/01.pdf   (том 71, выпуск 1, 2026, статья 1)
  https://www.imim.pl/files/archiwum/Vol4_2023/09.pdf   (том 68, выпуск 4, 2023, статья 9)

Использование:
  python download_imim_pdfs.py --destination ./pdfs                          # скачать всё
  python download_imim_pdfs.py --destination ./pdfs --volume 71 --year 2026  # только Vol71_2026
  python download_imim_pdfs.py --destination ./pdfs --dry-run                # только показать
  python download_imim_pdfs.py --destination ./pdfs --grab-links links.txt   # сохранить ссылки
"""

import argparse
import logging
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

BASE_URL = "https://www.imim.pl/archives"
FILES_BASE = "https://www.imim.pl/files/archiwum"

# Регулярка для парсинга ссылок на PDF со страницы выпуска
# Пример: /files/archiwum/Vol1_2026/01.pdf
PDF_URL_PATTERN = re.compile(
    r"/files/archiwum/Vol(?P<issue>\d+)_(?P<year>\d{4})/(?P<number>\d+)\.pdf$"
)

# Регулярка для парсинга ссылок на выпуски из бокового меню.
# Текст ссылки: "Volume 71 Issue 1/2026" или "Volume 53 Issue 4/2008"
# Номер выпуска может включать букву (2A, 2B, 3A, 3B), но для PDF используется только число.
ISSUE_TEXT_PATTERN = re.compile(
    r"Volume\s+(?P<volume>\d+)\s+Issue\s+(?P<issue>\d+)[A-Za-z]?/(?P<year>\d{4})"
)

# User-Agent для имитации браузера
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}


# ---------------------------------------------------------------------------
# Модель данных для одного PDF
# ---------------------------------------------------------------------------
class PdfEntry:
    """Описывает один PDF-файл из архива."""

    __slots__ = ("volume", "issue", "year", "number", "url", "filename")

    def __init__(self, volume: int, issue: int, year: int, number: int, url: str):
        self.volume = volume
        self.issue = issue
        self.year = year
        self.number = number
        self.url = url
        self.filename = f"{number:02d}.pdf"

    def __repr__(self) -> str:
        return (
            f"PdfEntry(volume={self.volume}, issue={self.issue}, year={self.year}, "
            f"number={self.number}, url={self.url})"
        )


# ---------------------------------------------------------------------------
# Модель данных для одного выпуска (issue)
# ---------------------------------------------------------------------------
class IssueEntry:
    """Описывает один выпуск журнала (том/номер/год + URL страницы выпуска)."""

    __slots__ = ("volume", "issue", "year", "url")

    def __init__(self, volume: int, issue: int, year: int, url: str):
        self.volume = volume
        self.issue = issue
        self.year = year
        self.url = url

    def __repr__(self) -> str:
        return (
            f"IssueEntry(volume={self.volume}, issue={self.issue}, "
            f"year={self.year}, url={self.url})"
        )


# ---------------------------------------------------------------------------
# Загрузка страниц
# ---------------------------------------------------------------------------
def fetch_page(url: str, retries: int = 3) -> Optional[str]:
    """Загружает HTML-страницу с повторными попытками."""
    for attempt in range(1, retries + 1):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=30)
            resp.raise_for_status()
            return resp.text
        except requests.RequestException as e:
            log.warning("Попытка %d/%d: ошибка при загрузке %s: %s", attempt, retries, url, e)
            if attempt < retries:
                time.sleep(2 ** attempt)
    return None


# ---------------------------------------------------------------------------
# Парсинг главной страницы архива — сбор ссылок на выпуски
# ---------------------------------------------------------------------------
def parse_issue_links(html: str) -> list[IssueEntry]:
    """
    Извлекает из бокового меню главной страницы все ссылки на выпуски.

    Парсит левое меню (#lewe_menu ul.menu li a) и извлекает номер тома,
    номер выпуска и год из текста ссылки.
    """
    soup = BeautifulSoup(html, "html.parser")
    menu = soup.select_one("#lewe_menu ul.menu")
    if not menu:
        log.warning("Не найдено меню выпусков (#lewe_menu ul.menu)")
        return []

    issues: list[IssueEntry] = []
    seen_urls: set[str] = set()

    for a_tag in menu.find_all("a", href=True):
        href = a_tag.get("href", "")
        span = a_tag.find("span")
        text = span.get_text(strip=True) if span else a_tag.get_text(strip=True)

        match = ISSUE_TEXT_PATTERN.search(text)
        if not match:
            continue

        full_url = urljoin(BASE_URL, href)
        if full_url in seen_urls:
            continue
        seen_urls.add(full_url)

        entry = IssueEntry(
            volume=int(match.group("volume")),
            issue=int(match.group("issue")),
            year=int(match.group("year")),
            url=full_url,
        )
        issues.append(entry)

    return issues


# ---------------------------------------------------------------------------
# Парсинг страницы выпуска — извлечение PDF-ссылок
# ---------------------------------------------------------------------------
def parse_issue_page(html: str, issue: IssueEntry) -> list[PdfEntry]:
    """
    Извлекает все ссылки на PDF со страницы конкретного выпуска.

    Для каждой найденной PDF-ссылки извлекает номер статьи из URL,
    а том, выпуск и год берёт из IssueEntry (родительского выпуска).
    """
    soup = BeautifulSoup(html, "html.parser")
    entries: list[PdfEntry] = []
    seen_urls: set[str] = set()

    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"]
        match = PDF_URL_PATTERN.search(href)
        if not match:
            continue

        full_url = urljoin(issue.url, href)
        if full_url in seen_urls:
            continue
        seen_urls.add(full_url)

        entry = PdfEntry(
            volume=issue.volume,
            issue=issue.issue,
            year=issue.year,
            number=int(match.group("number")),
            url=full_url,
        )
        entries.append(entry)

    return entries


# ---------------------------------------------------------------------------
# Скачивание одного файла
# ---------------------------------------------------------------------------
def download_pdf(
    entry: PdfEntry,
    output_dir: Path,
    *,
    skip_existing: bool = True,
    retries: int = 3,
) -> bool:
    """
    Скачивает один PDF-файл.

    Сохраняет в: {output_dir}/{year}_{volume}/Volume_{issue:02d}/{number:02d}.pdf

    Возвращает True, если файл успешно скачан (или уже существует).
    """
    # Папка: {output_dir}/{year}_{volume}/Volume_{issue:02d}
    dest_dir = output_dir / f"{entry.year}_{entry.volume}" / f"Volume_{entry.issue:02d}"
    dest_dir.mkdir(parents=True, exist_ok=True)

    dest = dest_dir / entry.filename

    if skip_existing and dest.exists() and dest.stat().st_size > 0:
        log.debug("Пропуск (уже есть): %s", dest)
        return True

    for attempt in range(1, retries + 1):
        try:
            log.info("Скачивание [%d/%d]: %s", attempt, retries, entry.url)
            resp = requests.get(entry.url, headers=HEADERS, timeout=60, stream=True)
            resp.raise_for_status()

            # Проверка Content-Type
            content_type = resp.headers.get("Content-Type", "")
            if "application/pdf" not in content_type and not content_type.startswith("application/octet-stream"):
                log.warning(
                    "Неожиданный Content-Type: %s для %s", content_type, entry.url
                )

            # Сохраняем
            with open(dest, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)

            file_size = dest.stat().st_size
            if file_size == 0:
                log.warning("Файл пустой, удаляем: %s", dest)
                dest.unlink(missing_ok=True)
                continue

            log.info("  -> Сохранён: %s (%d байт)", dest, file_size)
            return True

        except requests.RequestException as e:
            log.warning("Ошибка при скачивании %s: %s", entry.url, e)
            if attempt < retries:
                time.sleep(2 ** attempt)
            # Удаляем недокачанный файл
            if dest.exists():
                dest.unlink(missing_ok=True)

    log.error("Не удалось скачать: %s", entry.url)
    return False


# ---------------------------------------------------------------------------
# Фильтрация
# ---------------------------------------------------------------------------
def filter_entries(
    entries: list[PdfEntry],
    *,
    volume: Optional[int] = None,
    year: Optional[int] = None,
) -> list[PdfEntry]:
    """Фильтрует записи по номеру тома и/или году."""
    result = entries
    if volume is not None:
        result = [e for e in result if e.volume == volume]
    if year is not None:
        result = [e for e in result if e.year == year]
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Скачивание PDF файлов с архива IMIM (https://www.imim.pl/archives)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Примеры:\n"
            "  %(prog)s --destination ./pdfs                         # скачать всё\n"
            "  %(prog)s --destination ./pdfs --volume 71 --year 2026  # только Vol71_2026\n"
            "  %(prog)s --destination ./pdfs --dry-run               # показать что будет скачано\n"
            "  %(prog)s --destination ./pdfs --workers 8             # 8 параллельных потоков\n"
            "  %(prog)s --destination ./pdfs --grab-links links.txt  # сохранить ссылки в файл\n"
        ),
    )
    parser.add_argument(
        "--volume",
        type=int,
        default=None,
        help="Номер тома (например, 71)",
    )
    parser.add_argument(
        "--year",
        type=int,
        default=None,
        help="Год (например, 2026)",
    )
    parser.add_argument(
        "--destination",
        "-d",
        type=Path,
        required=True,
        help="Папка для сохранения PDF (обязательно)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Количество параллельных потоков (по умолчанию: 4)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Только показать список файлов без скачивания",
    )
    parser.add_argument(
        "--grab-links",
        type=Path,
        default=None,
        metavar="FILE",
        help="Сохранить список всех найденных PDF-ссылок в файл (без скачивания)",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Подробный вывод (debug)",
    )
    return parser.parse_args(argv)


# ---------------------------------------------------------------------------
# Точка входа
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # 1. Загружаем главную страницу архива
    log.info("Загрузка страницы архива: %s", BASE_URL)
    html = fetch_page(BASE_URL)
    if html is None:
        log.error("Не удалось загрузить страницу архива. Проверьте подключение к интернету.")
        return 1

    # 2. Парсим ссылки на выпуски из бокового меню
    issues = parse_issue_links(html)
    if not issues:
        log.warning("Выпуски не найдены на странице. Возможно, изменилась структура сайта.")
        return 1

    log.info("Найдено выпусков: %d", len(issues))

    # 3. Фильтруем выпуски по тому и году (если указаны)
    if args.volume is not None:
        issues = [i for i in issues if i.volume == args.volume]
    if args.year is not None:
        issues = [i for i in issues if i.year == args.year]

    if not issues:
        log.warning(
            "Нет выпусков, соответствующих фильтру: volume=%s, year=%s",
            args.volume,
            args.year,
        )
        return 0

    log.info(
        "Отобрано выпусков для обработки: %d (volume=%s, year=%s)",
        len(issues),
        args.volume or "все",
        args.year or "все",
    )

    # 4. Для каждого выпуска загружаем страницу и собираем PDF
    all_entries: list[PdfEntry] = []
    for issue in issues:
        log.info("Загрузка страницы выпуска: Vol %d, Issue %d, %d — %s",
                  issue.volume, issue.issue, issue.year, issue.url)
        issue_html = fetch_page(issue.url)
        if issue_html is None:
            log.warning("Не удалось загрузить страницу выпуска: %s", issue.url)
            continue

        pdf_entries = parse_issue_page(issue_html, issue)
        log.info("  Найдено PDF: %d", len(pdf_entries))
        all_entries.extend(pdf_entries)

    if not all_entries:
        log.warning("PDF-файлы не найдены ни на одной странице выпусков.")
        return 1

    log.info("Всего найдено PDF-файлов: %d", len(all_entries))

    # 5. Dry-run
    if args.dry_run:
        log.info("Dry-run: файлы, которые будут скачаны:")
        for e in sorted(all_entries, key=lambda x: (x.volume, x.year, x.number)):
            rel_path = Path(f"{e.year}_{e.volume}") / f"Volume_{e.issue:02d}" / e.filename
            print(f"  {rel_path}  -> {e.url}")
        return 0

    # 5b. Grab-links — сохранить список ссылок в файл
    if args.grab_links is not None:
        links_file = args.grab_links.resolve()
        links_file.parent.mkdir(parents=True, exist_ok=True)
        log.info("Сохранение ссылок в: %s", links_file)
        with open(links_file, "w", encoding="utf-8") as f:
            for e in sorted(all_entries, key=lambda x: (x.volume, x.year, x.number)):
                f.write(e.url + "\n")
        log.info("Сохранено ссылок: %d", len(all_entries))
        return 0

    # 6. Скачиваем
    output_dir = args.destination.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    log.info("Сохранение в: %s", output_dir)

    success = 0
    failed = 0

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        future_map = {
            executor.submit(download_pdf, entry, output_dir): entry
            for entry in all_entries
        }

        for future in as_completed(future_map):
            entry = future_map[future]
            try:
                ok = future.result()
                if ok:
                    success += 1
                else:
                    failed += 1
            except Exception as exc:
                log.error("Исключение при скачивании %s: %s", entry.url, exc)
                failed += 1

    # 7. Итог
    log.info("=" * 50)
    log.info("Готово! Успешно: %d, Ошибок: %d, Всего: %d", success, failed, len(all_entries))
    log.info("Файлы сохранены в: %s", output_dir)

    if failed:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())