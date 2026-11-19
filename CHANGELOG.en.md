# Changelog

🇷🇺 Русская версия: [CHANGELOG.md](CHANGELOG.md)

What changed in Moodle Offline, version by version: new features, fixes, and anything that became safer. The newest version is at the top. To see which version you have, open the app's settings and look at the update panel (“Настройки” → “Обновление программы”); “Проверить сейчас” checks for a new version and “Обновить сейчас” installs it.

Moodle Offline is built for students of Sevastopol State University (SevSU) and its Moodle at `do.sevsu.ru`. **The app's interface is in Russian**, so button and tab names below are given in Russian in quotes, with an English description next to them where it helps.

**How to read this**
- The date is the day the version landed in the repository.
- “No separate release” means the version was never published as its own download; its changes shipped inside the next release named there, on the [Releases](https://github.com/JINXPIL/Moodle-Offline/releases) page.
- For the releases `v1.8.0`, `v1.12.0` and `v1.12.1`, the version number shown inside the app is lower than the tag (1.7.0, 1.11.0, 1.12.0) because the build did not take the version from the tag. Fixed in 1.13.0 — the number now comes from the tag.

**What was verified and what was not.** The university sites (`do.sevsu.ru`, `schedule.sevsu.ru`, `iot.sevsu.ru`, etc.) require a login, so they cannot be tested automatically. Behaviour was checked against test copies of the sites and pages sent in by the author; the interface was checked in Chromium (phone and desktop widths) and in an Android emulator. Details for each item are in the descriptions of the [closed pull requests](https://github.com/JINXPIL/Moodle-Offline/pulls?q=is%3Apr+is%3Aclosed). Not tested: a real Android phone, Windows with Edge, real Linux and Mac computers (the builds were only run on GitHub's servers), and real AI keys. If something works differently for you, please open an [issue](https://github.com/JINXPIL/Moodle-Offline/issues).

---

## 1.34.0 — 2026-11-19

**New**
- “Расписание” (Schedule): a “Columns” setting — **Auto / 1 / 2 / 3 / 4** — like the week grid on schedule.sevsu.ru, where the day cards sit in two columns and the whole week is visible at once. It works in the “Неделя” (Week) and “Список” (List) views; you choose it with the buttons under the size slider on the “Aa” button (and in the “Фильтры и настройки” sheet), or in the “Вид” (View) window → “Расписание” → “Колонок в неделе и списке”. “Auto” is the old behaviour (by screen width).
- With 2–4 columns the day card and the lessons become more compact: “Пн, 16 ноя” instead of “Понедельник, 16 ноября”, the time on one line “08:30 – 10:00”, and the lesson type, room and teacher in a smaller font; the building address is not shown in narrow columns (the room stays), and long names wrap by syllables. With 3–4 columns the date and “N пар” (N lessons) sit one under the other. If the text became too small or too tight, lower the text size (“Aa”) or take one column fewer; the “Плотность” (Density) setting still changes the padding as before.
- Columns are remembered like the text size: on the device, as one value for all schedules, and not carried between devices.

**What was verified and what was not.** Automatically — `tests/test_schedule_cols_unit.py` (page text, allowed values, `node --check`) and the shared-keys check in `tests/test_view_settings_unit.py`. In Chromium on a made-up timetable: 390, 360 and 320 pixel screens with touch and a 1280 desktop, Week and List views, all five values, text size 80 % and 100 %, both extreme densities — nothing sticks out of a card or past the screen edge and the page gets no horizontal scroll; a choice made in the “Вид” window reaches the schedule at once, and reset returns “Auto”. **Not verified:** a real Android WebView (there, syllable wrapping uses the system dictionaries), a live touch screen, Windows/Edge.

---

## 1.33.0 — 2026-11-19

**New**
- “Расписание” (Schedule) on a phone: the top bar is now a single row — “‹ date ▾ › · Today · Aa · filters”. The view (Day / Week / Month / List) is chosen by tapping the date. Before, the top row slid away while scrolling and came back only at the very top of the list, so the bar “jumped”; now it is always the same height and stays put. “Today” is a small calendar icon with the date number (highlighted when you are already on today's day or week).
- Text size is now continuous: pinch the schedule with two fingers, or use the slider under the “Aa” button (80 % to 180 %, with “A−/A+” and a reset button). Before, there were four preset steps hidden in the filter sheet, and the top bar grew together with the text. Now only the lessons scale, the bar stays the same, and whatever is under your fingers stays in place while pinching. The “Вид” (View) window shows the current size as a percentage (for example “123 %”).
- More room for lessons: when there is only one schedule, the row for choosing the group is hidden (settings open from the “Источники и обновление…” button in the “Фильтры и настройки” sheet inside the schedule), the banners on this tab are thinner, and the lesson window takes exactly the space left above the bottom tabs. Before, its height was computed as “screen minus 52 pixels”, so the “showing another group's schedule” strip could spill past the edge of the screen.
- “День” (Day) view: the date is no longer repeated under the bar (it is already in the header) — a short line like “4 пары · 08:30–15:20” remains. On the narrowest screens (320 pixels) the titles are shorter (“чт · сегодня”, “16 — 22 ноя”) so they fit. Phone in landscape: the row and the week strip sit side by side.

**What was verified and what was not.** Automatically — `tests/test_schedule_bar_unit.py` (page text, allowed size values, parsing the viewer script). In Chromium on a made-up timetable: 390, 360 and 320 pixel screens with touch, phone in landscape, dark theme, desktop 1280; the bar keeps its height while scrolling in every view; the view menu, the slider, “A−/A+”, reset, closing by tapping outside; a pinch (two fingers, emulated touch) changes the size, does not zoom the app page and keeps the same element under the fingers; the app's settings sheet opens from the viewer; with several schedules the picker row comes back. **Not verified:** a real Android WebView and a live touch screen (two-finger scrolling there may jitter slightly), Windows/Edge.

---

## 1.32.1 — 2026-11-18

**Fixed**
- “Элективы” (sports electives): when you picked a different option, its tile got no outline, no tick appeared, and the summary before saving read “Основной: NaN”. The site's option numbers are strings, not numbers, and the program mangled them on selection. Now the pick shows at once, the summary names the options, and exactly what the site gave is what is sent back. The same fix covers signing up for a class and withdrawing from one.
- “Элективы”: when the site did not answer, “Сохранить на сайте” (save on the site) and signing up for a class could hang for a very long time with no cancel button — the program waited for the site to come back. Now a site that does not answer is recognised within ten to forty seconds, a strip “Связываюсь с сайтом…” (contacting the site) with a “Отмена” (cancel) button shows under the header, and after a few seconds it honestly says the site is slow. After a cancel or a failure the program says nothing was sent to the site; if the request might have got through, it says so — press “Обновить” and check your choice on the site.
- A failure of the electives site no longer turns the header indicator to “Moodle недоступен” (Moodle unavailable): it is a different site (iot.sevsu.ru) and has nothing to do with Moodle.
- “Успеваемость” (grades) on a wide screen: an empty strip the width of a side column — which this tab does not have — was left to the right of the list. The list now fills the whole window.

**What was verified and what was not.** Cancelling and the quick failure for an unreachable site were tested automatically with a stand-in client and against a closed port (an unreachable site fails in under twenty seconds, “Отмена” breaks the wait within seconds), and the window in Chromium on made-up data with string option numbers: choosing, the summary, saving, signing up, withdrawing, a 390 px phone and a 1920/1280/1000 px computer. The real iot.sevsu.ru, a real phone (Android WebView) and Windows/Edge were not tested.

---

## 1.32.0 — 2026-11-18

**New**
- “Элективы” (sports electives) is shorter and easier on a phone: options are now tiles in two columns (three or four on a computer) with the name, a free-places bar and a tick; with a single main pick you just tap the tile. Before, every option was a wide dropdown about 200 pixels tall, one per row. On the test data the page is about a third shorter (8,700 → 5,800 pixels), and a block of fifteen options went from 2,170 to 1,560. When a block needs several roles (main picks and reserves), a list “Не выбрано / Основной 1 / Резерв 1…” stays at the bottom of the tile.
- The “Обновить” (refresh) button is in the tab header instead of the bottom of the side column; sign-in, login and password, “Следить за изменениями” (watch for changes) and the page address moved into a collapsible “Вход и слежение” panel (collapsed on a phone, it opens by itself when a password is needed).
- Section chips “Выбор / Занятия / Мои блоки” (choose / classes / my blocks) stick under the header and scroll to the section. A block with six or more options gets a filter “Все / Свободные / Выбрано” (all / with places / chosen) and a search by name. At the bottom of a block, a bar like “Выбрано 3 из 4, осталось: резерв 2” holds the save button: it stays in view while a long list scrolls and sits above the bottom tabs.
- Upcoming classes are grouped by day (“Сегодня”, “Завтра”) with a filter “Можно записаться / Вы записаны” (can sign up / signed up); the first twelve show, the rest under “Показать все” (show all). The selection deadline shows how much time is left (“ещё 20 дней”, in red when under a week).
- Past and closed blocks take one line with the result (“Вы распределены: …”); options and “Открыть на сайте” (open on the site) appear on tap. “Ваш выбор” (your choice) and “Вы распределены” (you are allocated) are two small cards. On a computer, class days run in two columns.
- “Вид” (view settings) for electives gets two new items: whether to show options with no places left, and the description under an option.

**What was verified and what was not.** The tab's new look was checked in Chromium on made-up data: screens of 412, 390, 360 and 320 pixels with touch, a large system font (zoom 1.4), light and dark themes, a 1280 px computer — choosing and un-choosing, several roles, filters and search, scrolling to sections, the bar above the bottom tabs, “Вид”, and opened blocks staying open when the window refreshes. Choosing, signing up for classes and logging in on the real iot.sevsu.ru are still verified only against a mock of the site; a real phone (Android WebView) and Windows with Edge were not tested.

---

## 1.31.0 — 2026-11-18

**New**
- “Успеваемость” (academic record) is now comfortable on a phone: a compact summary card on top (average grade, how many subjects are passed, credits, a progress bar, how many 5s, 4s and 3s), then a strip of semesters (“2 семестр · 8 из 14”) that stays under the header while you scroll, a filter “Все / С оценкой / Ждут” (all / graded / waiting) and a search across all semesters at once. Subjects are tidy cards: a coloured edge by grade, a round grade badge on the right, and a dashed “ждёт” (waiting) card for subjects not yet passed.
- The update buttons, the password box and “Удалить сохранённое” (delete saved) moved into a collapsed block “Обновление и вход” (update and sign-in) under the list — it opens by itself when there is no data yet or a sign-in is needed. “Обновить” (update) stays at the top.
- On a phone the tab label is “Оценки” instead of the truncated “Успевае…” (on a computer it is still “Успеваемость”).

**Fixed**
- Long subject names did not fit on a phone screen: the table had to be scrolled sideways and a big round arc was drawn over the rows. The cause was that the grades table shared its name with the grade badge on a course card and picked up its rules. The tab now has its own names, there is no table at all, and long names wrap.

**What was verified and what was not.** The window was checked in Chromium on a made-up page (screen widths 390, 360 and 320 px, large system font, dark theme, a 1280 px computer) and by a text test. A real Android WebView and Windows/Edge were not checked; nor was signing in to the real lk.sevsu.ru.

---

## 1.30.0 — 2026-11-17

**New**
- “Старые версии программы” (old program versions): the settings window has a new panel that finds earlier downloaded files of the program itself — `.exe`, `.apk`, archives, checksum files and update leftovers (`.old`, `.new`, unfinished downloads) — in Downloads, on the desktop, in Documents and next to the program; on a phone, in shared storage (this needs “all files” access, and the program explains how to give it). A list with checkboxes on both computer and phone: “Выбрать все” (select all), “Снять все” (clear all) and “Удалить выбранные” (delete selected; it asks first, and files are deleted for good, not moved to a bin). Old versions and leftovers are ticked for you; files newer than your version, identical ones, and ones the program could not judge are left unticked. The version comes from the file name, and from the file itself for `.apk`; for an `.exe` without a number in its name the program does not guess and leaves the decision to you. The program that is running right now is never shown or touched. After start-up it tells you once if it found something old.
- Notices in the window can be dismissed without waiting: a cross on the right, a swipe sideways (on a phone or tablet), and “Закрыть все” (close all) when there are several. While the pointer or a finger is on a notice it does not disappear by itself; no more than five are stacked on screen.

**Security**
- Only files that a fresh search of the usual folders just showed can be deleted; a foreign path slipped into a request is refused. Folders and symbolic links are never deleted (regular files only), and the search needs no internet.

---

## 1.29.1 — 2026-11-16

**Fixed**
- “Успеваемость” (Academic record) showed an empty “Программа 1” with an advertisement instead of grades: after sign-in the portal sometimes opens its home page (tabs and ads), and the program took any page with tabs and tables for the grade sheet. Grades are now taken only from the grade-sheet page; if another page opens, the program asks for the grade sheet once more, and if that fails it shows a clear error and saves a diagnostic log (addresses and counts, no page text). A snapshot saved from the wrong page is replaced on the next refresh, with no bogus “new grade” notices.

---

## 1.29.0 — 2026-11-16

**New: “Успеваемость” (academic record)**
- A new “Успеваемость” tab: your electronic record from the lk.sevsu.ru personal account (“Сведения об успеваемости”) — every semester of the study plan with subject, type of assessment, grade, credits (ZET), hours and date. At the top: average grade, how many subjects are passed and how many credits are earned; the last semester that already has grades opens first.
- “Обновить с сайта” signs in to the personal account with your SevSU login (the same way electives and the schedule do). If the sign-in fails or the site changed, open the page in a browser, save it (Ctrl+S, “Web page, HTML only”) and press “Из файла…”.
- The record is saved in the “Успеваемость” folder (the data plus a page that opens without the app and without internet). If the site is down, what is saved stays. A new or corrected grade adds an entry to “Что нового” (the grade itself is not sent to external notifications by default). “Удалить сохранённое” erases everything.
- The “Успеваемость” folder is not moved to other devices, is not searchable, and is closed to external AI tools. The average grade is calculated by the app from grades 5–2 (pass/fail credits are not included) and may differ from the official figure.

**Schedule: Excel files from the university site**
- Fixed: on the real sevsu.ru/univers/shedule page the links to the spreadsheets look like `download.php?file=…` without “.xlsx” (the file icon marks them as Excel), so the old search would have found no files there at all. Links are now found, and each keeps its headings: academic year, kind of schedule, institute and semester. A remembered choice survives a new year or semester; for part-time (ZFO) groups the session table comes first.
- File updates: every time the schedule is saved, the table is compared with the saved one. The app says “file unchanged” or “file updated: N classes added, M removed” (and adds an entry to “Что нового”). A new file replaces the saved one only after it is checked to contain your group.
- If the university site does not answer, returns an error, the page was rebuilt, or garbage comes back instead of a file, the app stays on the saved file; the window shows “site unavailable, schedule from the file saved on <date>”, and when the site works again the file is refreshed on its own. Until a file has been saved at least once, the error is shown as before.

**Security**
- The password for “Успеваемость” is sent only to https pages on sevsu.ru; if a login form points to a foreign address the app refuses to submit it. The sign-in log contains only addresses and form names — no password, no grades, no name.

**For developers**
- New module `lk.py` (page parsing, figures, comparison, storage, sign-in); tests `test_lk_unit.py`, `test_lk_api_unit.py`, `test_excel_update_unit.py` on made-up pages with the real markup. Not verified: signing in to the real lk.sevsu.ru, a real `download.php` download, Windows/Edge, an Android phone.

## 1.28.0 — 2026-11-15

**Fixed**
- Course names and categories on course cards are now shown in full. Before, a long name was cut off with an ellipsis (“Английский язык (Уровень Pre-Intermedi…”), so two similar courses could not be told apart.
- This was most noticeable on a phone with the picture turned off (“Вид” → “Картинка: скрыть”): the text was pushed to the right and the name had only about 110 px left. Now the selection checkbox sits in the corner, the name uses the full width, and the badge (“скрыт” = hidden, “прошлый” = past, “будущий” = upcoming) and the category no longer overlap. Long words wrap instead of sticking out past the card edge.

**For developers**
- Android emulator check: the “https” item now waits until the emulator has a network connection (on the 1.27.0 release this caused a build failure and the apk files were not published). A certificate error is still not retried.

## 1.27.0 — 2026-11-15

**Schedule**
- The app no longer gives new users the author's group by default. Your own group comes from the settings or from an already downloaded schedule; if there is neither, the app asks you to choose one.
- Changing the group no longer mixes schedules: the previous group's file is not continued, and the saved page does not inherit another group's name. The new group is downloaded right away.
- If a schedule for a different group is what's on disk, a notice says so.
- A mistyped group: instead of a long wait and “0 classes” you now get a clear “group … does not exist on the site” message, and no empty entry is left in the list.
- “ИБ/б-25-2-о” (as the university's Excel file writes it) and “ib-b-25-2-o” (as on the site) are treated as the same group.
- On a phone, “＋ Добавить группу” (“Add group”) opens the settings sheet (before, it scrolled a hidden block).

**Settings no longer disappear**
- “Вид” (View), the theme, the accent colour, the schedule view and the other window settings used to reset on every launch (the window gets a new address each time, and the browser stores them per address). They are now kept by the program itself and survive restarts and updates.
- A new “Сброс” (Reset) panel in the settings: “Сбросить вид” (reset the view) and “Сбросить всё” (reset everything). Reset-everything keeps your login, download folder, course list, group, AI settings and autostart; a copy of the previous settings is saved as `settings.json.before-reset`.

**Security**
- `.html` / `.svg` files from course materials (for example forum attachments) used to open inside the app window on the app's own address, so a script in them could reach the program and launch a downloaded `.exe`. Such files now open in a sandbox with no access to the program (the app's own schedule viewer is the only exception).
- The program accepts requests only from its own window.
- The program no longer launches `.exe`, `.bat`, `.ps1`, `.lnk`, `.jar` and similar files — only “Показать в папке” (show in folder) is available for them.

**File reliability**
- Settings have a backup copy, `settings.json.bak`; a damaged file is set aside (`.broken-…`) and the settings are restored from the backup.
- Settings, state and keys are written to disk with a forced flush; simultaneous writes each use their own temporary file (before, they shared one).
- A file briefly locked by antivirus software or OneDrive is read again. Before, “Что нового” (What's new), the calendar or the download state could start from scratch because of this. Unreadable data is never overwritten.

*Version 1.26.0 is part of this release.*

## 1.26.0 — 2026-11-15

No separate release — shipped in v1.27.0.

**AI**
- Under the proxy field there is now a “how requests will go” line: system proxy found / direct / system SOCKS (the app does not use it — enter the HTTP port of the same client) / proxy from the field. “Проверить связь” (check connection) adds the route to its answer.
- If a SOCKS proxy is set in the system, you now get an explanation in plain language instead of the cryptic `InvalidSchema` error.
- For VPN clients (Happ, Clash Verge, etc.) the “system proxy” or TUN mode is enough. Checked against fake servers, not against the real clients.

## 1.25.0 — 2026-11-15

**“Курсы” (Courses) tab**
- The right-hand column is gone. On a computer, “Скачать выбранные (N)” (download selected) and “Синхронизация” (sync) sit in the row above the cards, and the cards take the full width (seven per row at 1920 px instead of six).
- On a phone, while a course is selected, a bar with the same buttons floats above the bottom tabs (before, they were at the very end of a long list).
- “Синхронизация” is the former “Быстрая синхронизация (только новое)” (quick sync, new items only); the full name is in the tooltip.

**Fixed**
- In a 1280 px window the page was wider than the window (1364 px) and the right edge was cut off.

## 1.24.0 — 2026-11-14

- **Settings behind a gear icon** in the header, on every tab. The window holds four blocks that used to sit at the bottom of “Курсы”: “Как сохранять” (how to save), “Бережный режим” (gentle mode), “Доступ для нейросетей” (access for AI tools) and “Обновление программы” (program update). The “new version available” banner opens the settings directly on the update block.
- **Phone:** the Moodle status is a short word (“Онлайн”, “Медленно”, “Недоступен”, “Пауза” — online, slow, unavailable, paused), with the full text on tap; the header always stays on one line.
- The course filters (“Текущие / Прошлые / Скрытые / Все” — current / past / hidden / all) on a narrow screen are two rows of two buttons instead of a scroll.
- Course cards adapt to a large system font: with enlarged text a card takes a whole row instead of breaking.

## 1.23.0 — 2026-11-14

- **“Вид” (View)** — a button in every section that opens a settings sheet of “item — choice” rows. Courses (cards or list, sorting, density, picture, columns on phone, grouping by category, what to show, “downloaded only”), the course window, schedule, notes, search, “Что нового”, calendar, electives. You can reorder the tabs and hide the ones you don't need (“Курсы” cannot be hidden). The settings are stored on the device and are not transferred between devices.
- Schedule: text size, density, colour labels and the set of fields in a class — the same settings as inside the schedule viewer itself.

## 1.22.1 — 2026-11-14

- Phone: the app window has its own **scrollbar thumb you can grab and drag**. The system scrollbar only showed your position and did not react to touch.

## 1.22.0 — 2026-11-14

**Linux and macOS**
- Ready-made builds in Releases: Linux as a single file; macOS as `MoodleOffline.app` in a `.zip`, for Apple Silicon and for Intel (unsigned: macOS may ask for permission on first launch).
- Notifications, a folder-picker window, autostart of the watcher at login, nightly sync (Linux — a systemd timer, macOS — a LaunchAgent).
- “Обновить сейчас” (update now) on Linux (with SHA-256 verification); on macOS you update manually.
- Data on macOS lives in `~/Library/Application Support/MoodleOffline`.
- Described in the README, section 7г.

**Fixed**
- On a Mac the window and “Перенос данных” (data transfer) could hang for ~35 seconds at startup waiting for a reverse DNS lookup. No longer.
- Not tested: the real `osascript`, `zenity`, `notify-send`, `systemctl --user`, `launchctl`, launching the `.app` from Finder, Gatekeeper.

## 1.21.0 — 2026-11-13

- **Access for external AI tools.** Programs such as Claude Code, Cursor or VS Code can search and read your downloaded materials (and, with a separate checkbox, your notes). They come to the app; the app itself sends nothing anywhere.
- Read-only — there are no actions that write. **Off by default.** It works only on this computer, with a token; service folders, hidden files and anything outside the materials folder are blocked.
- A “Доступ для нейросетей” panel (in Settings): on/off, port, token, and a log of recent requests (without their text).
- Not tested: real clients (Claude Code, Cursor, VS Code) — the protocol was checked with the project's own tests.

## 1.20.0 — 2026-11-13

- **AI inside the app** (the “Нейросеть” window): ask a question about your own materials and get an answer with links to the files, a short summary of a file, or practice questions (3–20, answers behind a button).
- It works with your own key: Google Gemini, Claude (Anthropic), OpenAI, or “your own address” in the OpenAI format (OpenRouter, Ollama, LM Studio). The app has no AI of its own. You can copy a result or save it to your notes.
- **Privacy.** Until you choose a service, enter a key and give consent, nothing is sent. A request goes out only when you press a button; the AI is never called in the background. The amount of text that can be sent is capped. The key is stored separately from the settings (encrypted on Windows) and is never transferred between devices.
- Not tested: real Gemini/Claude/OpenAI keys — only fake AI servers.

## 1.19.3 — 2026-11-13

**Fixed**
- Course announcements were losing topics: with “save the last N” set above 10, the first 10 were saved and the rest were treated as already known and never saved; if more than 10 new ones appeared at once, the extras only got a line in “Что нового”. Announcements are now saved 10 pages per pass (gentle on the server), the remainder is queued and picked up on the following updates. Up to 100 latest topics are scanned (before, 30).

## 1.19.2 — 2026-11-13

No separate release — shipped in v1.19.3.

**Fixed**
- In the calendar, “Посещаемость (Группа …)” (attendance) entries with a class time were treated as assignment deadlines and ended up in reminders and the `.ics` file. They are now a separate kind, “Занятия из «Посещаемости»” (sessions from Attendance) — a class time, not a deadline. Off by default; turn it on in the calendar settings.

## 1.19.1 — 2026-11-13

- Notes: the buttons (pin, show in folder, delete) are on the right and checkboxes on the left. “Select all”, a counter, “Delete selected” with a confirmation and a “Return” (undo) button; Shift + checkbox selects a range. Selection applies only to visible notes: filtered-out notes are never deleted.

**Fixed**
- The trash's “last 50” was counted by the note's edit date rather than the deletion time, so a note you had just deleted but not edited for a long time could vanish at once.
- A note without the service line can now be restored from the trash.

## 1.19.0 — 2026-11-13

- **“Заметки” (Notes) tab:** a general notebook and per-class notes right in the course window. Works offline and without logging in to Moodle.
- Notes are ordinary `.md` files in the `Заметки/` folder, so any editor can open them. Autosave, pinning, a trash with a “Return” button.
- The notes folder is indexed for “Поиск” (Search); notes travel between devices through “Перенос данных” (merged file by file, the newer one wins).

## 1.18.3 — 2026-11-13

**Fixed**
- The program quit while its window was still open and showed “Программа закрыта — перезапустите её” (“The program is closed — restart it”). A minimized browser window barely polled the program, so it assumed the window was gone. Now the program exits only when the window has said goodbye and has not come back within 15 seconds (the fallback is 12 hours of silence; if the computer was asleep, the wait is extended).
- A failure inside the program no longer drops the connection: the window gets an “error” answer with the reason, and the failure is written to `errors.log` and the “Журнал” (log) without passwords, keys or cookies.

**New**
- A **“Перезапустить программу”** (restart the program) button (under “Дополнительно” and in the error banner): the new copy asks the previous one to close and takes its place.
- The window tells three cases apart: “Нет связи с программой” (no connection — the program is closed; it says what to do), “Программа ответила ошибкой” (the program answered with an error — restart button) and “Окно не смогло показать ответ” (the window could not display the answer).
- The update check now reports the reason when it fails.

## 1.18.2 — 2026-11-13

- Phone (.apk): the folder button and “Показать в папке” open the system “Files” app at the right place (before, they only showed the path). Not tested on real phones.

**For developers**
- Automated tests now live in the repository and run in GitHub Actions on Python 3.11 and 3.13.

## 1.18.1 — 2026-11-13

**Fixed**
- Phone: the orange “Офлайн-режим — Moodle недоступен” (offline mode — Moodle unavailable) banner took about a third of the screen. The banners (offline, session expired, VPN) are now one thin line: a title and “Войти” (log in); tapping the title expands the explanation and buttons. The “update available” banner is thinner, and the schedule selector row is lower.

## 1.18.0 — 2026-11-13

**Phone**
- “Загрузки” (Downloads) is an icon in the header (with the percentage on it while a download runs) and a thin strip under the header (blue — running, yellow — paused, red — waiting for the server). Tapping it opens a full-screen sheet: “Файлы / Ошибки / Журнал” (files / errors / log), the overall bar, the current file and the “Пауза / Пропустить файл / Стоп” (pause / skip file / stop) buttons (they were not visible on phones before). Close it with the cross or the system Back button. When a download finishes, the icon shows a dot: green, or red if there were errors.
- When a download starts, the panel no longer pops up by itself: a hint is shown once.
- The schedule fills the whole screen: the page does not scroll, settings are a separate sheet, filters are a bottom sheet.
- The IP badge and the avatar in the header are hidden on a phone (the VPN warning remains as a banner under the header).

## 1.17.2 — 2026-11-12

**Fixed**
- After an update, an empty “Программа закрыта — перезапустите её” window from the previous version stayed on screen (versions before 1.17.1 cannot close themselves). The new copy now closes such windows itself (Windows). Not tested on a real Windows machine.

## 1.17.1 — 2026-11-12

**Fixed**
- After “Обновить” (update) two windows remained: the new one and the old one saying “Программа закрыта”. The old one now closes by itself.
- The `.old` file next to the program is removed with retries (Windows does not release the file while the previous process is still shutting down).

## 1.17.0 — 2026-11-12

**Course “as on the site”** (based on real do.sevsu.ru pages)
- **Completion marks:** next to an item — “✓ выполнено на сайте” (done on the site) / “не выполнено на сайте” (not done) / “✕ не пройдено” (failed); the course header shows a total such as “выполнено 6 из 20” (6 of 20 done).
- **Access restrictions:** for a section and an item, the reason it is locked is shown (“Недоступно, пока не выполнено: …” — unavailable until … is completed).
- **Attendance:** a table of classes (date, time, description, status, points), a line like “was · was not · ahead or not marked · percentage”, absences in red. The page is opened in the “Все” (All) mode rather than only the current month. The tabs and month/week switches that lead to the site are removed from the offline copy.
- **Grades:** after a download, one request to the “Оценки” (Grades) overview; the grade is shown on the course card. It can be turned off with the “save grades” setting (`--no-grades` in the console).
- Not tested: Moodle 4.x and the per-course grade report.

## 1.16.2 — 2026-11-12

**Fixed**
- Starting the program a second time just opened the window of the already running old copy: a downloaded new version “showed the old one” and the “please update” banner never went away. Now the new copy asks the old one to close and takes its place.

**New**
- **“Обновить сейчас”** (update now; Windows `.exe`, button only): downloads the release file next to the program, checks its SHA-256, size and file type, then “Перезапустить и обновить” (restart and update). If the release has no `.sha256` file, or the program folder is not writable, there is no button — the manual way remains.

## 1.16.1 — 2026-11-12

- **The Android app in four variants:** universal `MoodleOffline.apk` (installs on any phone), `MoodleOffline-arm64-v8a.apk` (modern phones, the smallest), `MoodleOffline-armeabi-v7a.apk` (older 32-bit phones), `MoodleOffline-x86_64.apk` (emulators, ChromeOS). All of them share one signature.

## 1.16.0 — 2026-11-11

- **Other groups' schedules.** Above the schedule there is a list: “Моё” (mine), the groups you added, and “＋ Добавить другую группу…” (add another group). It is fetched from schedule.sevsu.ru (and optionally the university's Excel file), with no login; your own data is untouched. Each group keeps its own viewer settings.
- “Обновлять вместе с моим” (update together with mine; off by default), deletion, and the console mode `--schedule … --group … --other`.
- **Share:** one `.zip` made of the schedules you tick (inside: an `index.html` that opens without the program, an `.ics` file and the data). The archive contains no passwords, cookies or settings. The recipient can load such a `.zip` into their own copy of the program.

## 1.15.0 — 2026-11-11

No separate release — shipped in v1.16.0.

- **File reserve.** Teachers sometimes replace a lecture with a “stub” (a tiny PDF); Moodle then changes the file's address, and the program used to download the new file over the old one — the lecture was lost. Now, before replacing, the previous version is moved to `<Course>/_Резерв/` (named “… (заменён 2026-11-11 18-30)”), and the three latest versions are kept.
- A **“looks like a stub”** flag: the new file is 5 or more times smaller than the old one (starting from 20 KB), or empty.
- “Что нового” gets a “Файл заменён” (file replaced) event with an “Открыть прежний” (open the previous one) button; in the course window the earlier versions are listed under the file.
- On by default (`--no-reserve` turns it off in the console). The same file downloaded again is not copied. Quick sync re-checks files no more than once a day, otherwise a replacement would go unnoticed during auto-update.

## 1.14.3 — 2026-11-11

No separate release — shipped in v1.16.0.

**Phone**
- “Элективы” (Electives): a block card no longer slides sideways, and the status wraps under the name.
- Bottom menu: the tabs share the width equally and the labels no longer run together.
- On a tablet or a narrow window the header wraps onto a second line, and a long full name is shortened.

## 1.14.2 — 2026-11-11

**Fixed**
- Phone: in the “Просмотр” (Preview) window (for example, the table of contents of a lecture package) the buttons did not fit and the “close” cross slid off the edge — the window could not be closed. The header now wraps onto two lines.
- The system Back button closes the top window, then the course (before, it only minimized the app).

## 1.14.1 — 2026-11-11

**Fixed**
- After “Перенос данных” (data transfer, computer → phone) the courses looked as if nothing was downloaded, although the files had arrived. Courses found in the download folder are now picked up automatically.

**For developers**
- The apk check in the emulator no longer hangs: if the emulator does not come back after a reboot, that item is marked “NOT VERIFIED” and the build does not fail.

## 1.14.0 — 2026-11-11

- **Schedule from the university's Excel file** (sevsu.ru/univers/shedule): a new source, “Excel (семестр)”. The program finds the file on the site itself; you can also pick it from a list, paste a link, or upload a file by hand. Group blocks, subgroups, lecture streams and the building (from the “Справка” sheet) are all read.
- **“Загрузки” (Downloads):** an overall bar up to 100 %, speed and time left; files are grouped by course and the groups collapse.
- **Fixed:** HTML course packages (“Просмотр”) opened empty because their scripts and styles did not load. They work now.
- Android: text in a course is no longer squeezed into a narrow column; a redundant disk write after each file was removed.
- The update check goes through the system proxy and errors are written in plain language.
- Not tested: the real sevsu.ru page with the Excel file (only mock pages), and the WebView inside the `.apk`.

## 1.13.0 — 2026-11-11

- **Program update check** (the “Обновление программы” panel): “Проверить сейчас” (check now), an automatic check once a day (on by default; period from 6 hours to a week), “Пропустить эту версию” (skip this version), a banner and a notification about a new version (once per version). One request goes to `api.github.com`, nothing personal. A “how to update” hint for `.exe`, `.apk` and Termux.
- **Electives after a download go through the site's API.** The old lines “Кнопки записи на странице не найдены” (sign-up buttons not found on the page) were written by the old browser-based method.
- The phone-friendly schedule (from 1.12.1, below) is part of this release.
- The build now takes the version number from the tag (before, tag v1.12.1 had 1.12.0 inside the program).

## 1.12.1 — 2026-11-11

No separate release — shipped in v1.13.0.

- **The schedule adapts to a phone:** “День / Неделя / Месяц / Список” (day / week / month / list) views, a week strip, swipe, text size, a one-line header, a window sized to the screen height.
- The saved schedule page refreshes itself after the program is updated (it is rebuilt from the data, with no internet needed).

## 1.12.0 — 2026-11-11

- **Electives through the iot.sevsu.ru API.** The program logs in through the single SevSU account without a browser, so the “Элективы” tab works on a phone too. You can see: selection blocks with their status, options and free places, your choice, the allocation result, upcoming classes.
- **“Main + reserves by priority” choice** (don't choose / Main / Reserve 1 / Reserve 2), a summary before saving, signing up for a class and cancelling — only when you press the button. The program does not check places and conditions on the site's behalf — it shows the site's answer as is.
- The watcher reports a new block, an opened selection, the allocation and new classes. The old method (a page snapshot in a background Edge) remains as a fallback.
- **A “reader” in the course:** any item (assignment, page, forum, quiz, folder) has an “Открыть здесь” (open here) button: the page is cleaned to a safe form, and links, pictures and files go through the program's login. Actions (submitting work, replying, starting a quiz) stay on the site. What you have read is saved and opens without internet; if the site is down, the saved copy is shown.
- Not tested: login and sign-up on the real iot.sevsu.ru (login is closed) — only a mock.

## 1.11.0 — 2026-11-11

- **Data transfer between devices** (computer ↔ phone) without logging in to Moodle and without a password: over Wi-Fi (a QR code and an address; only what is new is taken, with resuming from where it broke off) or as one `.zip` file.
- You choose what to transfer: materials, schedule, course list, calendar and deadlines, “Что нового”, electives, settings (group, rules, selected courses).
- **Never transferred:** the password, login and cookies, the Telegram token, the webhook address, paths, the proxy. Sharing over Wi-Fi works only on a private network, with a code, for 15 minutes.

## 1.10.0 — 2026-11-11

No separate release — shipped in v1.11.0.

- **Phone:** the tabs are a bottom bar with icons and badges; “Загрузки” is compact and collapsible; search inside PDFs works on a phone (a fallback library, `pypdf`).
- **Schedule:** if the site published the schedule only part of the way to the end of the period, the window says “выложено до дд.мм (N-я неделя)” (published up to dd.mm, week N). A new setting, “Насколько вперёд обновлять” (how far ahead to update): 2 weeks / a month / to the end of the semester.
- **Background Edge/Chrome:** three ways to launch it and the reason for a failure in the log (exit code and the browser's message) — in response to reports of “the browser did not start” and “PDF failed”.

## 1.9.0 — 2026-11-11

- **Android app (`.apk`):** the program's window lives inside the app, and a foreground service keeps the server and the timer running; notifications, access to “Documents”, autostart. The build and an emulator check run automatically in GitHub Actions.

## 1.8.0 — 2026-11-11

No separate release — shipped in v1.9.0.

- **Android through Termux:** the program runs on a phone and its window opens in the browser. Notifications, autostart (Termux:Boot), a materials folder in shared storage; installation by the `install_termux.sh` script. The password is not saved on Android (there is no protected storage).

## 1.7.0 — 2026-11-11

Published in Releases as `v1.8.0` (the program itself says 1.7.0).

- **“Таймер и автообновление” (timer and auto-update) window:** a “is Moodle alive” check with a configurable frequency (30 s … 3 h), days and hours; “Moodle не отвечает” / “снова работает” (Moodle is not responding / is working again) messages in “Что нового” and as notifications.
- **Auto-update of missing files** (off by default): on a timer it compares the files on disk with the program's memory and downloads only what is missing — for all courses, the selected ones, or only those already downloaded.

## 1.6.0 — 2026-11-11

- **Login, settings and downloads survive a version change.** The `.exe`'s data now lives in the user folder (`%LOCALAPPDATA%\MoodleOffline`) rather than next to the file, so you can put a new version anywhere. An empty `portable.txt` next to the program brings back the old “USB stick” mode.
- On first launch a new version looks for the previous version's data by itself (next to it, in neighbouring folders, on the desktop, in “Downloads” and “Documents”) and copies it. Before, moving the program lost the login and the materials were downloaded again.

## 1.5.0 — 2026-11-11

- **“Календарь” (Calendar) tab:** assignment and quiz deadlines from Moodle and schedule classes in one agenda (overdue → today → tomorrow → further by day). Filters, a “сделано” (done) mark, a counter on the tab.
- A `Календарь/moodle_calendar.ics` file for Google, Apple and Outlook: deadlines with reminders (a day and 3 hours before) and classes (15 minutes before). There is also an offline page, `Календарь/index.html`.
- Notifications: a new deadline, a deadline moved, a deadline burning.

## 1.4.0 — 2026-11-11

- **“Поиск” (Search) tab:** search the text inside downloaded files (PDF, Office and LibreOffice documents and spreadsheets, presentations, HTML, plain text). Works offline and without logging in to Moodle.
- It understands word forms (a search for “алгоритм” finds “алгоритмов”), `"exact phrases"` and `-exclusions`; a result is a file with up to three highlighted snippets, and a PDF opens at the right page. Filters by course and type, sorting by relevance or newness. Identical files are shown once.

## 1.3.0 — 2026-11-11

- **“Что нового” (What's new) tab and notifications.** After a download or sync the program compares the course with last time and writes to a feed: new materials and files, **teacher announcements** (saved in “Course/Объявления” along with attachments), new and changed grades, and things removed from the site.
- Notifications: a Windows pop-up, Telegram, a webhook (JSON, Discord, Slack, ntfy). You choose which events, which courses to mute, and there is a “Проверить отправку” (test sending) button. The first check of a course reports nothing and only memorizes it.

## 1.2.0 — 2026-11-10

- **“Правила скачивания” (download rules):** only / except certain extensions (groups `@документы` documents, `@видео` video, `@архивы` archives), a size limit, turning off item types (quizzes, folders, packages…), skipping sections and names by word or expression. Set for all courses or separately for each; ready-made sets “Только документы” (documents only), “Без видео и архивов” (no video or archives), “Экономия места” (save space).
- **Mode 3, “Свой шаблон” (custom template):** folders and file names built from a template (`{section_no}`, `{kind}`, `{seq}`, `{item}`…). In the console: `--dir-template`, `--file-template`.
- What was skipped by the rules is not an error: it is listed in `_пропущено_правилами.txt` and marked in the course's table of contents. Anything already downloaded is not deleted; if you loosen the rules, quick sync fetches the rest.

## 1.1.1 — 2026-11-10

- **Schedule** from two sites: the official `schedule.sevsu.ru` and the personal `timetable.sevsu.ru`; choose a group (in Cyrillic or Latin, or department → group). The month view is no longer cut off on the right.
- **Electives** (physical education) on `iot.sevsu.ru` — through a background browser; diagnostic files if the sign-up buttons are not found.
- **Offline mode** and single sign-on through “СевГУ.Аккаунт” (SevSU Account).
- A `.gitignore` so that personal data, cookies and downloaded materials never end up in the repository.

## 1.0.0 — 2026-11-09

First version.
- Downloading Moodle course materials (`do.sevsu.ru` by default): files, folders with nested structure, practical/lab assignments, pages and books, quizzes (description and your finished attempts), IMS/SCORM lecture packages, text blocks and links.
- A course view “as on the site” right in the window; a group schedule with an `.ics` file for your calendar; sports electives.
- **Resilience:** a file is downloaded as `.tmp` and renamed only after it is complete; on a 502 error or a lost connection the program waits and resumes where it stopped. Gentle on the server: one thread, pauses, “sleep” when the server is overloaded.
- A graphical interface (light and dark theme), a console version, and a build into a single `.exe`.
