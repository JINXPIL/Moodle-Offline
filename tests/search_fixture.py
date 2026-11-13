"""Собирает тестовую папку «Материалы» с настоящими файлами разных форматов (для проверки поиска)."""
import os, shutil, subprocess, sys, zipfile
from pathlib import Path

root = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/sfx/Материалы")
if root.exists():
    shutil.rmtree(root)
work = Path("/tmp/sfx_work"); shutil.rmtree(work, ignore_errors=True); work.mkdir(parents=True)

def mk(p):
    p = root / p; p.parent.mkdir(parents=True, exist_ok=True); return p

# ── Word ──
import docx
d = docx.Document()
d.add_heading("Лекция 2. Сортировка и поиск", 1)
d.add_paragraph("Быстрая сортировка делит массив опорным элементом. Сложность алгоритма — O(n log n) в среднем.")
d.add_paragraph("Ёмкость стека при рекурсии зависит от глубины разбиения.")
t = d.add_table(rows=2, cols=2); t.cell(0, 0).text = "Метод"; t.cell(0, 1).text = "Сложность"; t.cell(1, 0).text = "Пузырьковая"; t.cell(1, 1).text = "квадратичная"
d.save(work / "Лекция 2 Сортировка.docx")

d = docx.Document()
d.add_heading("Лекция 1. Введение в алгоритмы", 1)
d.add_paragraph("Алгоритм — конечная последовательность шагов. Понятие асимптотической сложности вводится здесь.")
for i in range(40):
    d.add_paragraph(f"Абзац {i}: подробности про структуры данных, массивы, списки и деревья.")
d.add_page_break()
d.add_paragraph("На второй странице обсуждается рекурсия и её применение: факториал и числа Фибоначчи.")
d.save(work / "Лекция 1 Алгоритмы.docx")

# ── PDF (из docx через LibreOffice — настоящий PDF с кириллицей) ──
subprocess.run(["soffice", "--headless", "--convert-to", "pdf", "--outdir", str(work), str(work / "Лекция 1 Алгоритмы.docx")],
               check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
# ── ODT и старый DOC ──
subprocess.run(["soffice", "--headless", "--convert-to", "odt", "--outdir", str(work), str(work / "Лекция 2 Сортировка.docx")],
               check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
subprocess.run(["soffice", "--headless", "--convert-to", "doc", "--outdir", str(work), str(work / "Лекция 2 Сортировка.docx")],
               check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

# ── PowerPoint ──
import pptx
pr = pptx.Presentation()
for title, body, note in [("Базы данных", "Реляционная модель и нормальные формы", "Не забыть про первичный ключ"),
                          ("SQL запросы", "SELECT, JOIN и агрегатные функции", ""),
                          ("Транзакции", "ACID: атомарность, согласованность, изолированность, долговечность", "Пример с банковским переводом")]:
    s = pr.slides.add_slide(pr.slide_layouts[1]); s.shapes.title.text = title; s.placeholders[1].text = body
    if note:
        s.notes_slide.notes_text_frame.text = note
pr.save(work / "Базы данных.pptx")
subprocess.run(["soffice", "--headless", "--convert-to", "odp", "--outdir", str(work), str(work / "Базы данных.pptx")],
               check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

# ── Excel ──
import openpyxl
wb = openpyxl.Workbook(); ws = wb.active; ws.title = "Группа ИБ"
ws.append(["Студент", "Оценка", "Комментарий"])
ws.append(["Иванов Пётр", 92, "Отличная лабораторная работа"]); ws.append(["Сидорова Анна", 78, "Нужна доработка"])
ws2 = wb.create_sheet("Зачёт"); ws2.append(["Вопрос", "Тема"]); ws2.append(["Что такое хеш-функция?", "Криптография"])
wb.save(work / "Журнал.xlsx")
subprocess.run(["soffice", "--headless", "--convert-to", "ods", "--outdir", str(work), str(work / "Журнал.xlsx")],
               check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

# ── Скан без текста: PDF из картинки ──
from reportlab.pdfgen import canvas
from PIL import Image, ImageDraw
im = Image.new("RGB", (400, 300), "white"); ImageDraw.Draw(im).text((20, 20), "scan page", fill="black"); im.save(work / "s.png")
c = canvas.Canvas(str(work / "Скан методички.pdf")); c.drawImage(str(work / "s.png"), 50, 500, 300, 225); c.save()

# ── Раскладка «как у программы» ──
A, B = "Курс А. Алгоритмы", "Курс Б. Базы данных"
shutil.copy(work / "Лекция 1 Алгоритмы.pdf", mk(f"{A}/01_Введение/ЛК_01 Лекция 1.pdf"))
shutil.copy(work / "Лекция 1 Алгоритмы.pdf", mk(f"{B}/02_Повторение/Лекция 1 (копия).pdf"))   # тот же файл → «дубль»
shutil.copy(work / "Лекция 2 Сортировка.docx", mk(f"{A}/02_Сортировка/ЛК_02 Сортировка.docx"))
shutil.copy(work / "Лекция 2 Сортировка.odt", mk(f"{A}/02_Сортировка/ЛК_02 Сортировка.odt"))
shutil.copy(work / "Лекция 2 Сортировка.doc", mk(f"{A}/02_Сортировка/Старая версия.doc"))
shutil.copy(work / "Базы данных.pptx", mk(f"{B}/01_Модели/ЛК_01 Базы данных.pptx"))
shutil.copy(work / "Базы данных.odp", mk(f"{B}/01_Модели/ЛК_01 Базы данных.odp"))
shutil.copy(work / "Журнал.xlsx", mk(f"{B}/03_Оценки/Журнал.xlsx"))
shutil.copy(work / "Журнал.ods", mk(f"{B}/03_Оценки/Журнал.ods"))
shutil.copy(work / "Скан методички.pdf", mk(f"{B}/04_Методички/Скан методички.pdf"))
mk(f"{A}/03_Практика/ПЗ_01 заметки.txt").write_bytes("Практическое занятие: напишите функцию сортировки слиянием.\nЁлочка — тестовое слово.".encode("cp1251"))
mk(f"{A}/03_Практика/sort.py").write_text("def merge_sort(items):\n    # сортировка слиянием\n    return sorted(items)\n", encoding="utf-8")
mk(f"{A}/03_Практика/данные.csv").write_text("имя;значение\nальфа;1\nбета;2\n", encoding="utf-8-sig")
mk(f"{A}/Объявления/2026-11-01 Важное.html").write_text(
    '<html><head><meta charset="utf-8"><title>Важное объявление</title><style>p{color:red}</style></head>'
    '<body><script>var secret_token_xyz = 1;</script><p>Зачёт переносится на пятницу. Приносите зачётные книжки.</p></body></html>', encoding="utf-8")
mk(f"{A}/02_Сортировка/ЛК_03 Страница курса.html").write_text(
    '<html><body><div class="mo-meta">Курс А. Алгоритмы · шапка_программы<br>Сохранено 11.11.2026 · <a href="http://x">открыть на сайте</a></div><h1>Страница курса</h1><p>Содержимое страницы про деревья.</p></body></html>', encoding="utf-8")
mk(f"{A}/Видео/запись лекции 3.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 100)
mk(f"{A}/index.html").write_text("<html><body>оглавление_служебное</body></html>", encoding="utf-8")   # генерируется программой → пропуск
mk(f"{A}/README.md").write_text("# оглавление_служебное", encoding="utf-8")
mk(f"{A}/Пакет SCORM/imsmanifest.xml").write_text("<manifest/>", encoding="utf-8")
mk(f"{A}/Пакет SCORM/index.html").write_text("<html><body>внутренности_пакета</body></html>", encoding="utf-8")
mk("index.html").write_text("<html><body>корневой_служебный</body></html>", encoding="utf-8")
mk("_журнал.log").write_text("служебный_лог", encoding="utf-8")
mk(".moodle_state.json").write_text("{}", encoding="utf-8")
mk("Расписание/index.html").write_text("<html><body>расписание_служебное</body></html>", encoding="utf-8")
mk("_бэкапы/копия.zip").write_bytes(b"PK\x05\x06" + b"\x00" * 18)
mk(f"{B}/05_Прочее/битый.docx").write_bytes(b"not a zip at all")
mk(f"{B}/05_Прочее/битый.pdf").write_bytes(b"%PDF-1.4 garbage")
print("fixture ok:", sum(1 for _ in root.rglob("*") if _.is_file()), "files →", root)
