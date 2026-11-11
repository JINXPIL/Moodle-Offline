#!/usr/bin/env bash
# Проверка собранного apk в эмуляторе Android (запускается из .github/workflows/build-apk.yaml).
# Ставит приложение, открывает его с флагом selftest (Python проверяет свои модули, SQLite, HTTPS, мост к Android), ждёт окно,
# сворачивает приложение и проверяет, что служба продолжает работать. Результаты — в папке smoke-out (логи и снимки экрана).
set -uo pipefail
APK="${1:?укажите путь к apk}"
PKG=ru.sevsu.moodleoffline
OUT=smoke-out
mkdir -p "$OUT"
fail=0
say() { echo "=== $*"; }

say "установка"
adb install -r "$APK" || { echo "НЕ УСТАНОВИЛОСЬ"; exit 1; }
adb shell pm grant "$PKG" android.permission.POST_NOTIFICATIONS || true
adb logcat -c

say "запуск с самопроверкой"
adb shell am start -n "$PKG/.MainActivity" --ez selftest true
for i in $(seq 1 100); do
  adb logcat -d > "$OUT/logcat.txt"
  grep -q "SELFTEST DONE" "$OUT/logcat.txt" && break
  grep -q "FATAL EXCEPTION" "$OUT/logcat.txt" && break
  sleep 3
done
grep -E "SELFTEST|FATAL|Traceback|AndroidRuntime" "$OUT/logcat.txt" | tee "$OUT/selftest.txt"
if grep -q "SELFTEST DONE ok=[0-9]* fail=0" "$OUT/selftest.txt"; then echo "самопроверка: ПРОЙДЕНА"; else echo "самопроверка: НЕ ПРОЙДЕНА"; fail=1; fi

say "окно"
sleep 12
adb exec-out screencap -p > "$OUT/screen1.png"
adb shell uiautomator dump /sdcard/ui.xml > /dev/null 2>&1
adb pull /sdcard/ui.xml "$OUT/ui.xml" > /dev/null 2>&1
if grep -q "Moodle" "$OUT/ui.xml" 2>/dev/null; then echo "в окне есть интерфейс программы: ДА"; else echo "в окне есть интерфейс программы: НЕТ"; fail=1; fi

say "свернули приложение — служба должна жить"
adb shell input keyevent KEYCODE_HOME
sleep 8
adb shell pidof "$PKG" > "$OUT/pid.txt" || true
if [ -s "$OUT/pid.txt" ]; then echo "процесс жив: ДА"; else echo "процесс жив: НЕТ"; fail=1; fi
adb shell dumpsys activity services "$PKG" > "$OUT/services.txt" 2>&1
if grep -q "ServerService" "$OUT/services.txt"; then echo "служба запущена: ДА"; else echo "служба запущена: НЕТ"; fail=1; fi

say "открыли снова"
adb shell am start -n "$PKG/.MainActivity"
sleep 6
adb exec-out screencap -p > "$OUT/screen2.png"
adb logcat -d > "$OUT/logcat-final.txt"
if grep -q "FATAL EXCEPTION" "$OUT/logcat-final.txt"; then echo "падений приложения: ЕСТЬ"; fail=1; else echo "падений приложения: нет"; fi

say "итог: $([ $fail -eq 0 ] && echo ОК || echo ПРОВАЛ)"
exit $fail
