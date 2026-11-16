# -*- coding: utf-8 -*-
"""Вымышленные страницы успеваемости (вёрстка как у lk.sevsu.ru) — общие для test_lk_unit.py и test_lk_api_unit.py."""
import _root

NAME = "Тестов Тест Тестович"             # такого имени у страницы быть не должно в сохранённом
CSRF = "FAKE-CSRF-TOKEN-0123456789"


def row(n, subject, control, grade="", zet=3, hours=108, date=""):
    st = 'style="font-weight: bolder;"'
    return (f'<tr>\n            <td {st}>{n}</td>\n            <td {st}                    class="text-left">{subject}</td>\n'
            f'            <td {st}>{control}</td>\n            <td {st}>{grade}</td>\n            <td {st}>{zet}</td>\n'
            f'            <td {st}>{hours}</td>\n            <td {st}>{date}</td>\n        </tr>\n')


def page(sems, prog="10.03.01 - 10.03.01 Информационная безопасность", extra_progs=None, select=False):
    """sems: [(заголовок вкладки, [строки html])]"""
    def container(i, sems):
        tabs = "".join(f'<li class="nav-item"><a class="nav-link {"active" if k == 0 else ""}" data-toggle="tab" href="#tab-{i}-{k}">{t}</a></li>'
                       for k, (t, _) in enumerate(sems))
        panes = ""
        for k, (t, rows) in enumerate(sems):
            panes += (f'<div id="tab-{i}-{k}" class="tab-pane fade in {"active show" if k == 0 else ""}"><div class="table-responsive">'
                      '<table class="table table-striped"><thead><tr><th>#</th><th>Предмет</th><th>Вид контроля</th><th>Оценка</th>'
                      '<th>ЗЕТ</th><th>Часы</th><th>Дата</th></tr></thead><tbody>' + "".join(rows) + '</tbody></table></div></div>')
        return (f'<div id="gc-{i}" class="grade-container {"active" if i == 0 else ""}"><ul id="grade-tabs-{i}" class="nav nav-tabs">{tabs}</ul>'
                f'<div id="tab-content-{i}" class="tab-content">{panes}</div></div>')
    body = container(0, sems)
    if extra_progs:
        body += "".join(container(i + 1, s) for i, s in enumerate(extra_progs))
    sel = ""
    if select:
        sel = '<select id="speciality-select"><option value="gc-0" selected>Первая программа</option><option value="gc-1">Вторая программа</option></select>'
    head = "" if select else f'<h4 class="grade-header-title"><strong>Специальность:</strong> {prog}</h4>'
    return ('<!DOCTYPE html><html><head><meta name="csrf-token" content="%s"></head><body>'
            '<span class="profile-info-text">%s</span><h3>Сведения об успеваемости</h3><div class="grade-widget">'
            '<div class="grade-header">%s%s</div><div class="grade-body">%s</div></div></body></html>' % (CSRF, NAME, head, sel, body))


S1 = [
    row(1, "Алгоритмы и структуры", "Экзамен", "Удовлетворительно", 8, 288, "26.01.2026"),
    row(2, "Основы права", "Зачет дифференцированный", "Отлично", 3, 108, "17.01.2026"),
    row(3, "Теория информации", "Экзамен", "Отлично", 6, 216, "22.01.2026"),
    row(4, "Физическая культура", "Зачет", "Зачтено", 2, 72, "01.12.2025"),
    row(5, "Медиабезопасность", "Зачет дифференцированный", "Хорошо", 3, 108, "13.01.2026"),
]
S2 = [
    row(1, "Право", "Зачет", "", 2, 72, ""),
    row(2, "Алгоритмы и структуры", "Экзамен", "Хорошо", 8, 288, "05.06.2026"),
    row(3, "Курсовая по сетям", "Курсовая работа", "", 2, 72, ""),
    row(4, "История региона &amp; культуры", "Зачет", "Не зачтено", 2.5, 90, "10.06.2026"),
]
S3 = [row(1, "Защита ВКР", "Защита ВКР", "", 9, 324, "")]
PAGE = page([("Первый семестр", S1), ("Второй семестр", S2), ("Четвертый семестр", S3)])


S2b = [row(1, "Право", "Зачет", "Зачтено", 2, 72, "20.06.2026")] + S2[1:2] + [S2[2]] + [S2[3]]
