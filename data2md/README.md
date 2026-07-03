# data2md - конвертер данных в Markdown для repomix

Инструмент для приведения данных разных типов в Markdown перед repomix и локальной LLM.

## Поддерживаемые форматы

| Тип | Вход | Выход |
|-----|------|-------|
| Изображения | PNG, JPG, JPEG | Текстовое описание (Qwen2.5-VL) |
| Текст | PDF, DOCX, MD, TXT | Markdown со структурой |
| Таблицы | XLSX, XLS, CSV | Таблицы + статистика + метрики |

## Установка

bash:
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env

## Использование

python data2md.py --input ./данные --output ./repomix_input

python data2md.py --input ./данные --output ./repomix_input --ext-include .png,.xlsx --no-text --verbose

python data2md.py --input ./данные --output ./repomix_input --no-images --max-rows 20

python data2md.py --input ./данные --output ./repomix_input --ext-include .png,.pdf,.docx,.xlsx --no-stats --include-key-metrics --max-rows 100 --page-limit 10 --vision-model qwen2.5-vl:7b --verbose

python data2md.py --input ./данные --output ./repomix_input --vision-provider deepseek --vision-deepseek-key YOUR_KEY
python data2md.py --input ./данные --output ./repomix_input --vision-provider vk --vision-vk-key YOUR_KEY
python data2md.py --input ./данные --output ./repomix_input --vision-provider yandex --vision-yandex-key YOUR_KEY

python data2md.py --input ./данные --output ./repomix_input --corpus-builder yek

## Полный пайплайн

python data2md.py --input ./данные --output ./repomix_input
repomix --input ./repomix_input --output ./corpus.txt
cat ./corpus.txt | ollama run MetalGPT-1 "Сгенерируй 5 гипотез по снижению потерь Ni на основе этих данных."

yek --input ./repomix_input --output ./corpus.txt
cat ./corpus.txt | ollama run MetalGPT-1 "Сгенерируй 5 гипотез по снижению потерь Ni на основе этих данных."

## Параметры

--input       : входная директория (default: ./данные)
--output      : выходная директория (default: ./repomix_input)
--ext-include : только эти расширения (через запятую)
--ext-exclude : исключить расширения (default: .log,.tmp)
--no-images   : не обрабатывать изображения
--no-text     : не обрабатывать текст
--no-tables   : не обрабатывать таблицы
--max-rows    : строк из Excel в Markdown (default: 50)
--page-limit  : страниц PDF, 0 = все (default: 0)
--vision-model: модель Vision API (default: qwen2.5-vl:7b)
--include-stats: добавлять статистику (default: true)
--no-key-metrics: не извлекать метрики
--verbose     : подробный вывод

## Структура

data2md/
  .env.example       - пример конфигурации
  requirements.txt   - зависимости
  README.md          - документация
  data2md.py         - скрипт