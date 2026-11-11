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

# снимок экрана печатаем в журнал уменьшенным и в base64: так его можно посмотреть, не скачивая артефакты
show_image() {
  local name="$1"
  if command -v convert > /dev/null; then convert "$OUT/$name.png" -resize 360x -quality 55 "$OUT/$name.jpg"; else cp "$OUT/$name.png" "$OUT/$name.jpg"; fi
  echo "=== IMG $name BEGIN"; base64 -w 200 "$OUT/$name.jpg"; echo "=== IMG $name END"
}

say "окно"
sleep 12
adb exec-out screencap -p > "$OUT/screen1.png"
adb shell dumpsys window | grep -E "mCurrentFocus" | head -2
# окно в WebView для uiautomator «пустое» (текст страницы не отдаётся), поэтому смотрим журнал программы и сам снимок
adb logcat -d > "$OUT/logcat.txt"
grep -E "MoodleOffline:|chromium.*(Uncaught)" "$OUT/logcat.txt" | tail -20
show_image screen1
if grep -q "Окно приложения подключилось к программе" "$OUT/logcat.txt"; then echo "в окне есть интерфейс программы: ДА"; else echo "в окне есть интерфейс программы: НЕТ"; fail=1; fi
colors=$(convert "$OUT/screen1.png" -format "%k" info: 2>/dev/null || echo 0)
echo "цветов на снимке: $colors"
if [ "${colors:-0}" -gt 500 ]; then echo "снимок не пустой: ДА"; else echo "снимок не пустой: НЕТ"; fail=1; fi

say "свернули приложение — служба должна жить"
adb shell input keyevent KEYCODE_HOME
sleep 8
adb shell pidof "$PKG" > "$OUT/pid.txt" || true
if [ -s "$OUT/pid.txt" ]; then echo "процесс жив: ДА"; else echo "процесс жив: НЕТ"; fail=1; fi
adb shell dumpsys activity services "$PKG" > "$OUT/services.txt" 2>&1
if grep -q "ServerService" "$OUT/services.txt"; then echo "служба запущена: ДА"; else echo "служба запущена: НЕТ"; fail=1; fi

adb shell dumpsys notification --noredact > "$OUT/notifications.txt" 2>&1
if grep -q "Moodle Offline работает" "$OUT/notifications.txt"; then echo "постоянное уведомление службы: ДА"; else echo "постоянное уведомление службы: НЕТ"; fail=1; fi
if grep -q "Самопроверка: уведомления работают" "$OUT/notifications.txt"; then echo "уведомление из программы (мост): ДА"; else echo "уведомление из программы (мост): НЕТ"; fail=1; fi

say "открыли снова"
adb shell am start -n "$PKG/.MainActivity"
sleep 6
adb exec-out screencap -p > "$OUT/screen2.png"
show_image screen2
adb logcat -d > "$OUT/logcat-final.txt"
grep -E "MoodleOffline:|chromium.*(ERROR|Uncaught)" "$OUT/logcat-final.txt" | tail -20
if grep -q "FATAL EXCEPTION" "$OUT/logcat-final.txt"; then echo "падений приложения: ЕСТЬ"; fail=1; else echo "падений приложения: нет"; fi

say "автозапуск после включения телефона (самопроверка включила его)"
adb shell am force-stop "$PKG"
sleep 3
if adb shell pidof "$PKG" | grep -q "[0-9]"; then echo "после остановки процесс ещё жив: ПРОВЕРКА НЕ ЧИСТАЯ"; fi
adb logcat -c
adb shell am broadcast -a android.intent.action.BOOT_COMPLETED -p "$PKG"
for i in $(seq 1 15); do
  sleep 3
  adb logcat -d > "$OUT/logcat-boot.txt"
  grep -q "сервер приложения запущен" "$OUT/logcat-boot.txt" && break
done
if grep -q "сервер приложения запущен" "$OUT/logcat-boot.txt"; then echo "после BOOT_COMPLETED программа запустилась без окна: ДА"; else echo "после BOOT_COMPLETED программа запустилась без окна: НЕТ"; fail=1; grep -E "BootReceiver|ServerService|ForegroundService|MoodleOffline" "$OUT/logcat-boot.txt" | tail -15; fi

say "итог: $([ $fail -eq 0 ] && echo ОК || echo ПРОВАЛ)"
exit $fail
