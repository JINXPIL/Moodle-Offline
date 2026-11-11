# Moodle Offline

Локальное приложение (Python + веб-интерфейс на localhost, собирается в .exe через PyInstaller) для студента СевГУ:
скачивает материалы курсов Moodle (https://do.sevsu.ru), сохраняет расписание (schedule.sevsu.ru, timetable.sevsu.ru)
и записывает на элективы по физкультуре (iot.sevsu.ru). Должно работать и онлайн, и офлайн, в том числе когда Moodle недоступен.

## Файлы
- `moodle_offline.py` — ядро: вход, клиент Moodle, загрузка, докачка, ZIP-бэкап, консольная версия, `VERSION`.
- `gui.py` + `ui/index.html` — локальный веб-интерфейс и его API; офлайн-режим, сохранённый вход (шифрование DPAPI на Windows).
- `schedule.py` — расписание: официальный JSON-API schedule.sevsu.ru, личное timetable.sevsu.ru, просмотрщик (HTML со встроенными данными).
- `electives.py` — элективы: страница iot.sevsu.ru — это SPA, работает через браузер.
- `news.py` — «Что нового»: сравнение курса с прошлым разом, лента `.moodle_news.json`, текст и отправка уведомлений (Windows, Telegram, webhook); без Moodle-кода, страницы ему отдаёт `moodle_offline.py` (`Processor.news_pass`).
- `search.py` — «Поиск по материалам»: офлайн-индекс (SQLite FTS5) в `.moodle_search.sqlite` в папке с материалами, чтение PDF/Office/ODF/HTML/текста, разбор запроса; без Moodle-кода, сторонняя библиотека одна — `pypdfium2` (только PDF); индексацию запускают `moodle_offline.py` (`search_pass`) и `gui.py`.
- `deadlines.py` — «Календарь и дедлайны»: сроки из календаря Moodle (`core_calendar_*` через `MoodleClient.ajax`), хранилище `.moodle_deadlines.json`, напоминания (виды новостей `deadline_*`), повестка вместе с парами, `Календарь/moodle_calendar.ics` и офлайн-страница; без Moodle-кода и без сторонних библиотек, запросы делает `moodle_offline.py` (`calendar_sync`, `calendar_watch`).
- `watch.py` — таймер «жив ли Moodle» и автообновление: настройки (`settings["watch"]`), дни/часы, `Tracker` (упал/ожил → виды новостей `moodle_down`/`moodle_up`), `auto_due`, замок `watch.lock`, состояние `watch.json`, автозапуск Windows (VBS в папке автозагрузки); без сети и сторонних библиотек, запросы делают `gui.py` (пока открыто окно) и `moodle_offline.py --watch` (`cli_watch`, `auto_update`, `audit_downloads`).
- `android.py` — Android (Termux): `is_android`, открытие файлов/ссылок, уведомления `termux-notification`, `termux-wake-lock`, автозапуск Termux:Boot, папка материалов в общей памяти; окно — браузер телефона, сервер на 127.0.0.1; пароль на Android не сохраняется (нет DPAPI); без сторонних библиотек; `install_termux.sh` ставит всё в Termux.
- `apk_main.py` + `android-app/` — приложение `.apk`: Gradle-проект (Java, Chaquopy 17, Python 3.13, только arm64-v8a и x86_64), окно — WebView с тем же `ui/index.html`, служба переднего плана держит сервер `gui.py` и таймер, мост `Bridge.java` ↔ `android.py` (флаг `MOODLE_OFFLINE_APK=1`; уведомления, файлы, wake lock, автозапуск, доступ к «Документам»). Python-файлы и `ui/` копируются в приложение из корня при сборке — отдельной копии кода нет; версия из `VERSION`. Подпись — из секретов (`android-app/make_keystore.py`), ключи не коммитить. Собирается и проверяется в эмуляторе только в GitHub Actions (`build-apk.yaml`, `android-app/ci/smoke.sh`): локально Maven/Google недоступны.
- `datadir.py` — где лежат данные (`.moodle_offline`): у .exe — папка пользователя (`%LOCALAPPDATA%\MoodleOffline`), `portable.txt` — рядом с exe; поиск и копирование данных прошлой версии при первом запуске (`prepare_data_dir`); без сторонних библиотек.
- `dl_rules.py` — правила скачивания (что брать, что пропускать) и шаблоны имён папок/файлов (режим 3); без сторонних библиотек.
- `cdp.py` — свой минимальный клиент DevTools Protocol для фонового Edge/Chrome (без сторонних библиотек).
- `*.bat`, `run.sh` — запуск и сборка. `.github/workflows/build-exe.yaml` — сборка exe в GitHub Actions: вручную (артефакт) или по тегу `v*` (ещё и выкладывает exe в Releases). `.github/workflows/build-apk.yaml` — то же для `MoodleOffline.apk` + проверка в эмуляторе Android.

## Правила
- Отвечать и писать комментарии/README по-русски. Объяснения короткие, простым языком.
- Вежливость к серверу: последовательные запросы, паузы, повторы с нарастающей задержкой, докачка. Не добавлять параллельную загрузку.
- Секреты не коммитить и не логировать: пароль, куки (`MoodleSession`, SSO), токен Telegram-бота и адрес webhook (`notify.bin`), содержимое `.moodle_offline/`, диагностические файлы с реальных страниц. Пароль вводится только на своём сайте (`sevsu.ru`, https).
- Сторонние библиотеки добавлять только при необходимости (список — `requirements.txt`).
- Менял поведение — обнови `README.md`; выпускаешь версию — поменяй `VERSION` в `moodle_offline.py`.
- Реальные сайты СевГУ требуют входа, поэтому автоматически их не проверить: проверяй на моках и честно пиши, что проверено, а что нет.
