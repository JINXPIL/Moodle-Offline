# Делает небольшой xlsx в том же устройстве, что у СевГУ (для тестов; реальные таблицы в репозиторий не кладём).
import io, zipfile
from xml.sax.saxutils import escape

def col(n):
    s = ""
    while n > 0:
        n, r = divmod(n - 1, 26); s = chr(65 + r) + s
    return s

def build(groups, weeks, legend=None):
    """groups: [имя]; weeks: [(лист, [(дата 'дд.мм.гггг', {номер_пары: {имя_группы: (kind, ...)}})])].
    kind: ('whole', текст, тип, ауд) | ('s1', текст, тип, ауд) | ('s2', текст, тип, ауд) | ('both', t1, ty1, a1, t2, ty2, a2)"""
    sheets = []
    for title, days in weeks:
        cells, merges = {}, []
        def put(r, c, v): cells[(r, c)] = v
        for gi, g in enumerate(groups):
            c0 = 1 + gi * 10
            put(4, c0, "Группа : " + g); merges.append(f"{col(c0)}4:{col(c0+9)}4")
            for k, lab in enumerate(["День", "Дата", "№занятия", "Время"]):
                put(5, c0 + k, lab); merges.append(f"{col(c0+k)}5:{col(c0+k)}6")
            put(5, c0 + 4, "подгруппа 1"); merges.append(f"{col(c0+4)}5:{col(c0+6)}5")
            put(5, c0 + 7, "подгруппа 2"); merges.append(f"{col(c0+7)}5:{col(c0+9)}5")
            for k, lab in enumerate(["Занятие", "Тип", "Аудитория"] * 2):
                put(6, c0 + 4 + k + (1 if k >= 3 else 0) - (0), lab) if False else None
            for base in (c0 + 4, c0 + 7):
                for k, lab in enumerate(["Занятие", "Тип", "Аудитория"]):
                    put(6, base + k, lab)
            r = 7
            times = ["8:30", "10:10", "11:50", "14:00", "15:40", "17:20", "19:00", "20:40"]
            for di, (date, lessons) in enumerate(days):
                put(r, c0, ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота"][di % 6]); merges.append(f"{col(c0)}{r}:{col(c0)}{r+7}")
                put(r, c0 + 1, date); merges.append(f"{col(c0+1)}{r}:{col(c0+1)}{r+7}")
                for n in range(1, 9):
                    rr = r + n - 1
                    put(rr, c0 + 2, str(n)); put(rr, c0 + 3, times[n - 1])
                    spec = (lessons.get(n) or {}).get(g)
                    if not spec: continue
                    kind = spec[0]
                    if kind == "whole":
                        put(rr, c0 + 4, spec[1]); merges.append(f"{col(c0+4)}{rr}:{col(c0+7)}{rr}")
                        put(rr, c0 + 8, spec[2]); put(rr, c0 + 9, spec[3])
                    elif kind == "s1":
                        put(rr, c0 + 4, spec[1]); put(rr, c0 + 5, spec[2]); put(rr, c0 + 6, spec[3])
                    elif kind == "s2":
                        put(rr, c0 + 7, spec[1]); put(rr, c0 + 8, spec[2]); put(rr, c0 + 9, spec[3])
                    elif kind == "both":
                        put(rr, c0 + 4, spec[1]); put(rr, c0 + 5, spec[2]); put(rr, c0 + 6, spec[3])
                        put(rr, c0 + 7, spec[4]); put(rr, c0 + 8, spec[5]); put(rr, c0 + 9, spec[6])
                r += 8
        sheets.append((title, cells, merges))
    legend = legend or [("I", "Учебный корпус по ул. Университетская, 33"), ("Д", "Дистанционное обучение")]
    leg_cells = {}
    for i, (a, b) in enumerate(legend):
        leg_cells[(3 + i, 2)] = a; leg_cells[(3 + i, 3)] = b
    sheets.insert(0, ("Справка", leg_cells, []))
    return _zip(sheets)

def _zip(sheets):
    buf = io.BytesIO()
    z = zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED)
    z.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="xml" ContentType="application/xml"/></Types>')
    wb = '<?xml version="1.0"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'
    rels = '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    for i, (title, cells, merges) in enumerate(sheets, 1):
        wb += f'<sheet name="{escape(title)}" sheetId="{i}" r:id="rId{i}"/>'
        rels += f'<Relationship Id="rId{i}" Type="x" Target="worksheets/sheet{i}.xml"/>'
        rows = {}
        for (r, c), v in cells.items(): rows.setdefault(r, []).append((c, v))
        x = '<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
        for r in sorted(rows):
            x += f'<row r="{r}">' + "".join(f'<c r="{col(c)}{r}" t="inlineStr"><is><t xml:space="preserve">{escape(v)}</t></is></c>' for c, v in sorted(rows[r])) + "</row>"
        x += "</sheetData>"
        if merges: x += f'<mergeCells count="{len(merges)}">' + "".join(f'<mergeCell ref="{m}"/>' for m in merges) + "</mergeCells>"
        x += "</worksheet>"
        z.writestr(f"xl/worksheets/sheet{i}.xml", x)
    wb += "</sheets></workbook>"; rels += "</Relationships>"
    z.writestr("xl/workbook.xml", wb); z.writestr("xl/_rels/workbook.xml.rels", rels)
    z.close()
    return buf.getvalue()
