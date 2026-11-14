# Проверки (tests)

Запуск всех одной командой, из корня проекта:

    python tests/run_all.py                 # все
    python tests/run_all.py news iot        # только те, в имени которых есть «news» или «iot»

То же самое в GitHub Actions делает задача **tests** (`.github/workflows/tests.yaml`): в каждом pull request, при слиянии в `main`
и вручную — на Python **3.11 и 3.13** (Linux) и на **3.13 в macOS**. Она **не мешает** сборкам apk, exe, Linux и macOS: упавший тест виден красным
крестом в pull request, но сборки идут как раньше.

## Что внутри
- `test_*.py` — отдельные проверки, каждая запускается своим процессом. Тест «провален», если вернул код не 0 или напечатал
  строку, начинающуюся с `FAIL`. Строки `PASS …` — что проверено.
- `_root.py` — подключает корень проекта (`import _root` — первая строка любого теста).
- `mock_moodle.py` — фейковый Moodle (разметка как у do.sevsu.ru, обезличена); `mock_iot.py` — фейковый iot.sevsu.ru (Keycloak + API).
  Нужные макеты `run_all.py` запускает сам (таблица `MOCKS` в нём) и останавливает после теста.
- `mock_llm.py` — фейковые нейросети (Claude, Gemini, OpenAI и совместимые, списки моделей) для `test_ai_unit.py` и `test_ai_api_unit.py`; ключи и тексты вымышленные, сервер поднимает сам тест в потоке.
- `test_mcp_unit.py` / `test_mcp_api_unit.py` / `test_mcp_stdio_e2e.py` — доступ для внешних нейросетей: инструменты и защита путей, HTTP на настоящем сокете (токен, Host/Origin, блокировка, лимиты), окно `/api/mcp*`, токен в `mcp.bin`, настоящий процесс `--mcp`; настоящих клиентов (Claude Code, Cursor…) тут нет.
- `test_desktop_unit.py` / `test_desktop_gui_unit.py` — Linux и macOS (`desktop.py`): команды `osascript`/`notify-send`/`zenity`/`kdialog` и защита текста в аргументах, файлы автозапуска (`.desktop`, LaunchAgent) и таймера systemd, включение/выключение на подставных `systemctl`/`launchctl`, окно (`/api/watch/autostart`, `/api/schedule`, `/api/pick_folder`). `test_updater_platforms_unit.py` — виды сборок, файлы релиза по архитектуре, проверка ELF, право на запуск. Настоящих macOS и systemd в этих тестах нет.
- `smoke_binary.py` — **не тест** (в `run_all.py` не входит): запускает уже собранную программу (Linux/macOS) без окна и проверяет, что она поднимается, отдаёт интерфейс и закрывается; запускается в `build-desktop.yaml`, у себя — `python tests/smoke_binary.py --system linux --kind linux --data-rel .local/share/moodle-offline -- ./dist/MoodleOffline`.
- `restart_helper.py` — запускалка для `test_restart_e2e.py`: та же `gui.gui_main`, но окно не открывается (вместо него файл-метка), а «перезапуск»
  запускает этот же файл. Два настоящих процесса (старая и новая копия) без интернета и без окна браузера.
- `apkfake/java.py` — подделка моста Android для проверки `apk_main.py` на компьютере.
- `mkxlsx.py`, `search_fixture.py` — сборка тестовых файлов (Excel-расписание; папка «Материалы» разных форматов для поиска).

## Что нужно
- `pip install -r requirements.txt` (больше ничего).
- Для `test_iot_unit.py` — в hosts строка `127.0.0.1 iot.sevsu.test auth.sevsu.test` (в Actions добавляется сама; у вас без неё тест
  «пропущен» с подсказкой, в Actions — ошибка).
- Для `test_search_unit.py` — LibreOffice (`soffice`) и `python-docx`, `python-pptx`, `openpyxl`, `reportlab`, `Pillow`. Без них тест
  «пропущен» — **в Actions он пока именно так и пропускается**, то есть поиск по материалам там не проверяется; проверяйте у себя.
- Необязательно: `MO_REAL_COURSE_PAGE=<путь к сохранённой настоящей странице курса>` (и `MO_REAL_USER_NAME=<имя на ней>`) — тогда
  `test_completion_unit.py` проверит ещё и её. Настоящие страницы в репозиторий не кладём (имя, `sesskey`).

## Чего тесты НЕ проверяют
- Настоящие сайты СевГУ (там нужен вход): только макеты. Тест проходит ≠ сайт не поменял разметку.
- Окно в браузере (Playwright), живую загрузку курса, Android WebView и сборку exe/apk — это отдельно (apk собирает и проверяет в эмуляторе
  `build-apk.yaml`; Linux и macOS собирает и запускает `build-desktop.yaml`).

## Как добавить тест
Файл `tests/test_что_проверяем.py`, первой строкой `import _root`, в конце `sys.exit(1 if FAILS else 0)`; печатать `PASS …` / `FAIL …`.
Если нужен макет сайта — добавьте его в таблицу `MOCKS` в `run_all.py`. Временные файлы — в `tempfile`, не в проекте и не по жёстким путям.
