#!/usr/bin/env python3
"""
Отладочный скрипт для изучения HTML страницы поиска rusneb.ru
"""
import requests
from bs4 import BeautifulSoup
import re

try:
    import cloudscraper
    session = cloudscraper.create_scraper()
    print("Используется cloudscraper")
except ImportError:
    session = requests.Session()
    print("Используется обычный requests")

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

url = "https://rusneb.ru/search/?q=%D0%BC%D0%B5%D1%82%D0%B0%D0%BB%D0%BB%D1%83%D1%80%D0%B3%D0%B8%D1%8F&access[]=open"

print(f"\nЗагружаем: {url}\n")
resp = session.get(url, headers=headers, timeout=30)
print(f"Status: {resp.status_code}")
print(f"Длина ответа: {len(resp.text)} символов")

html = resp.text

# Сохраняем HTML для анализа
with open("debug_page1.html", "w", encoding="utf-8") as f:
    f.write(html)
print("HTML сохранен в debug_page1.html")

# Ищем все ссылки с page=
soup = BeautifulSoup(html, "html.parser")
print("\n=== Все ссылки с page= ===")
page_count = 0
for a_tag in soup.find_all("a", href=True):
    href = a_tag["href"]
    if "page=" in href:
        page_count += 1
        print(f"{page_count}. {href} | текст: '{a_tag.get_text(strip=True)}' | class: {a_tag.get('class', [])}")

if page_count == 0:
    print("Ссылок с page= не найдено через BeautifulSoup")

# Регулярные выражения
print("\n=== Все URL с page= через regex ===")
for m in re.finditer(r'href=["\']([^"\']*page=\d+[^"\']*)["\']', html):
    print(f"  {m.group(1)}")

print("\n=== Все URL с catalog/ ===")
for m in re.finditer(r'href=["\']([^"\']*/catalog/[^"\']*)["\']', html):
    print(f"  {m.group(1)}")

# Ищем шаблоны пагинации
print("\n=== Пагинационные контейнеры ===")
pagination_divs = soup.find_all("div", class_=lambda c: c and "pagination" in " ".join(c).lower())
print(f"Найдено <div> с 'pagination' в классе: {len(pagination_divs)}")

pagination_uls = soup.find_all("ul", class_=lambda c: c and "pagination" in " ".join(c).lower())
print(f"Найдено <ul> с 'pagination' в классе: {len(pagination_divs)}")

# Ищем любые классы с "page" или "pager"
print("\n=== Элементы с page/pager/kendo в классе ===")
for tag in soup.find_all(True, class_=lambda c: c and any(kw in " ".join(c).lower() for kw in ["page", "pager", "kendo", "nav"])):
    print(f"<{tag.name}> class='{' '.join(tag.get('class', []))}'")