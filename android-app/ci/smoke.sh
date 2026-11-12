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
# какой Python внутри этого варианта (EXPECT_PY, например 3.11): так видно, что собран именно тот вариант, который заявлен
if [ -n "${EXPECT_PY:-}" ]; then
  if grep -q "SELFTEST OK libs: Python ${EXPECT_PY}\." "$OUT/selftest.txt"; then echo "Python в приложении ${EXPECT_PY}: ДА"; else echo "Python в приложении ${EXPECT_PY}: НЕТ"; fail=1; fi
fi

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

say "автозапуск: перезагружаем телефон (самопроверка включила автозапуск)"
# Настоящая перезагрузка: Android сам рассылает BOOT_COMPLETED и разрешает по нему запустить службу. Выдуманную рассылку через adb
# система не считает загрузкой и запуск службы запрещает, поэтому проверить автозапуск иначе нельзя.
# Эмулятор на сервере GitHub иногда не возвращается после перезагрузки (сам завершается). Раньше `adb wait-for-device` ждал его
# бесконечно, и задача висела до лимита (так было трижды). Теперь каждое ожидание ограничено по времени. Если эмулятор не вернулся,
# это сбой эмулятора, а не приложения: честно пишем «НЕ ПРОВЕРЕНО» и не валим сборку; остальные проверки выше уже прошли.
adb logcat -c   # чтобы «сервер приложения запущен» нельзя было засчитать по строке из журнала до перезагрузки
t0=$SECONDS
boot_ok=0
adb reboot
sleep 10
if timeout "${SMOKE_REBOOT_WAIT:-180}" adb wait-for-device; then
  for i in $(seq 1 60); do
    if [ "$(timeout 10 adb shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" = "1" ]; then boot_ok=1; break; fi
    sleep 5
  done
fi
if [ $boot_ok -eq 1 ]; then
  echo "телефон загрузился (за $((SECONDS - t0)) с)"
  for i in $(seq 1 30); do
    sleep 4
    timeout 60 adb logcat -d > "$OUT/logcat-boot.txt" 2>&1
    grep -q "сервер приложения запущен" "$OUT/logcat-boot.txt" && break
  done
  if grep -q "сервер приложения запущен" "$OUT/logcat-boot.txt"; then echo "после перезагрузки программа сама запустилась без окна: ДА"; else echo "после перезагрузки программа сама запустилась без окна: НЕТ"; fail=1; grep -iE "moodleoffline" "$OUT/logcat-boot.txt" | tail -30; fi
  timeout 60 adb shell dumpsys activity services "$PKG" > "$OUT/services-boot.txt" 2>&1
  if grep -q "ServerService" "$OUT/services-boot.txt"; then echo "служба после перезагрузки: ДА"; else echo "служба после перезагрузки: НЕТ"; fail=1; fi
else
  echo "::warning::Эмулятор не вернулся после перезагрузки за $((SECONDS - t0)) с: автозапуск не проверен (сбой эмулятора, не приложения). Запустите проверку ещё раз."
  echo "автозапуск после перезагрузки: НЕ ПРОВЕРЕНО (эмулятор не вернулся)"
  echo "эмулятор не вернулся после перезагрузки за $((SECONDS - t0)) с" > "$OUT/reboot-skipped.txt"
fi

say "итог: $([ $fail -eq 0 ] && echo ОК || echo ПРОВАЛ)$([ $boot_ok -eq 1 ] || echo ' (автозапуск после перезагрузки не проверен: сбой эмулятора)')"
exit $fail
