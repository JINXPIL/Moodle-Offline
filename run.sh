#!/bin/sh
# Linux / macOS:  ./run.sh            — графический интерфейс
#                 ./run.sh --cli      — консольная версия
#                 ./run.sh --sync     — быстрая синхронизация (для cron)
cd "$(dirname "$0")" || exit 1
if [ ! -x .venv/bin/python ]; then
  echo "Первый запуск: создаю окружение Python..."
  python3 -m venv .venv || { echo "Нужен Python 3.9+ с модулем venv (ALT: apt-get install python3-module-venv)"; exit 1; }
fi
if ! cmp -s requirements.txt .venv/requirements.installed; then
  .venv/bin/python -m pip install --disable-pip-version-check -q -r requirements.txt && cp requirements.txt .venv/requirements.installed || exit 1
fi
if [ "$1" = "--cli" ]; then shift; exec .venv/bin/python moodle_offline.py "$@"; fi
exec .venv/bin/python gui.py "$@"
