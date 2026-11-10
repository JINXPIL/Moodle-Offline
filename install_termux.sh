#!/bin/sh
# Установка Moodle Offline в Termux (Android). Запускать из папки программы:  sh install_termux.sh
# Что делает: ставит python и git, нужные библиотеки Python, просит доступ к памяти телефона и создаёт команду «moodle-offline».
# Ничего не удаляет и не трогает чужое. Повторный запуск безопасен (так же обновляются библиотеки).
cd "$(dirname "$0")" || exit 1
APP="$(pwd)"

say() { printf '%s\n' "$*"; }

if [ -z "${PREFIX:-}" ] || ! command -v pkg >/dev/null 2>&1; then
  say "Этот скрипт для Termux (Android). На компьютере используйте run.sh (Linux) или MoodleOffline.exe (Windows)."
  exit 1
fi

say "1/5. Пакеты Termux: python и git…"
pkg install -y python git || { say "Не получилось поставить python. Выполните «pkg update» и запустите скрипт снова."; exit 1; }

say "2/5. Библиотеки Python…"
python -m pip install --disable-pip-version-check -q requests beautifulsoup4 tqdm python-dotenv \
  || { say "Не получилось поставить библиотеки — проверьте интернет и запустите скрипт снова."; exit 1; }
if python -m pip install --disable-pip-version-check -q pypdfium2 >/dev/null 2>&1; then
  say "   pypdfium2 поставлена: поиск читает текст внутри PDF."
else
  say "   pypdfium2 на этом телефоне не установилась — поиск найдёт PDF только по названию. Всё остальное работает."
fi

say "3/5. Доступ к памяти телефона (чтобы материалы были в папке «Документы»)…"
if [ ! -d "$HOME/storage/shared" ] && command -v termux-setup-storage >/dev/null 2>&1; then
  say "   Сейчас Android спросит разрешение — нажмите «Разрешить»."
  termux-setup-storage
  i=0
  while [ ! -d "$HOME/storage/shared" ] && [ "$i" -lt 30 ]; do sleep 1; i=$((i + 1)); done
fi
if [ -d "$HOME/storage/shared" ]; then
  say "   Готово: материалы будут в «Документы/Материалы Moodle», их видно в приложении «Файлы»."
else
  say "   Доступ не выдан: материалы будут лежать внутри Termux (приложение «Файлы» их не увидит). Позже можно выполнить termux-setup-storage."
fi

say "4/5. Уведомления (необязательно)…"
if pkg install -y termux-api >/dev/null 2>&1; then
  say "   Пакет termux-api есть. Чтобы уведомления заработали, поставьте ещё приложение Termux:API (из F-Droid)."
else
  say "   Пропущено: уведомления можно получать в Telegram."
fi

say "5/5. Команда moodle-offline…"
BIN="$PREFIX/bin/moodle-offline"
if printf '#!/bin/sh\nexec sh "%s/run.sh" "$@"\n' "$APP" > "$BIN" && chmod 755 "$BIN"; then
  say "   Готово."
else
  say "   Не получилось создать команду. Запускайте так:  sh $APP/run.sh"
fi

say ""
say "Установлено. Запуск:      moodle-offline"
say "Слежение без окна:       moodle-offline --cli --watch -y"
say "Обновление программы:    cd \"$APP\" && git pull"
say "Подробности — в README.md, раздел «Android (Termux)»."
