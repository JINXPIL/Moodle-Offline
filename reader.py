# -*- coding: utf-8 -*-
"""
«Читалка» элементов курса (часть Moodle Offline): страница задания, форума, теста, папки, книги… внутри программы.

Берёт HTML страницы Moodle и делает из него безопасный чистый текст для показа в окне программы:
  • оставляет только основное содержимое (без шапки, меню, подвала);
  • выкидывает скрипты, формы и кнопки (сдавать работу, отвечать на форуме, проходить тест — это на сайте);
  • разрешает только безопасные теги и атрибуты — чужой скрипт из страницы не сможет выполниться;
  • ссылки не ведут никуда сами: каждая получает пометку, куда она идёт —
      data-rgo   — другая страница этого же Moodle (откроется здесь же),
      data-rfile — файл с этого Moodle (pluginfile.php и т. п.),
      data-rext  — другой сайт (откроется в браузере);
  • картинки и аудио/видео этого Moodle получают data-src (программа сама отдаёт их через свою сессию);
    картинки с чужих сайтов не подгружаются (чтобы чужой сервер не видел, что вы читаете).
Здесь нет сети и Moodle-кода: страницу отдаёт moodle_offline.py (через gui.py). Из сторонних библиотек — только bs4.
"""
from __future__ import annotations

import copy
import hashlib
import html as htmllib
import json
import re
import time
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

try:
    import lxml  # noqa: F401
    PARSER = "lxml"
except ImportError:
    PARSER = "html.parser"

MAIN_SEL = "#region-main [role='main'], [role='main'], #region-main, #page-content"
TITLE_SEL = ".page-header-headings h1, #page-header h1, header h1, h1"
STRIP_SEL = ("script, style, noscript, template, nav, button, input, select, textarea, label[for], object, embed, "
             ".activity-navigation, .modified, .skip-block, .accesshide, .sr-only, .visually-hidden, #page-footer, .footer, "
             ".modal, .dropdown-menu, .dropdown, [role='menu'], .breadcrumb, #page-navbar, [data-region='drawer'], "
             ".completion-info, .automatic-completion-conditions, [data-region='completion-info'], .tertiary-navigation, "
             ".region-main-settings-menu, .activity-header .completion-info, .editing_move, .moodle-actionmenu, .action-menu, "
             "[data-fieldtype], .filemanager, .fp-restrictions")
ALLOWED = {"p", "div", "span", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "li", "a", "img", "table", "thead", "tbody", "tfoot",
           "tr", "th", "td", "caption", "b", "strong", "i", "em", "u", "s", "br", "hr", "blockquote", "pre", "code", "sub", "sup",
           "dl", "dt", "dd", "details", "summary", "figure", "figcaption", "video", "audio", "source", "small", "mark", "abbr", "time", "section", "article", "header", "aside", "address"}
KEEP_ATTRS = {"colspan", "rowspan", "alt", "title", "lang", "dir", "start", "controls", "datetime"}
FILE_PATHS = ("/pluginfile.php", "/webservice/pluginfile.php", "/draftfile.php", "/tokenpluginfile.php", "/mod/resource/view.php")
SKIP_LINK_PATHS = ("/login/logout.php", "/login/index.php", "/user/edit.php", "/message/", "/my/", "/calendar/export",
                   "/post.php", "/startattempt.php", "/attempt.php", "/processattempt.php", "/togglecompletion.php", "/enrol/")
SKIP_QUERY_RX = re.compile(r"(^|&)(action=(editsubmission|submit|removesubmission|savesubmission|grader|grading)|delete=|reply=|edit=)")
DROP_PARAMS = {"sesskey"}
MAX_CACHE_PAGES = 400
READER_DIR = "reader"


def norm_url(url: str) -> str:
    """Адрес без якоря; параметр sesskey (одноразовый ключ сессии) вырезается."""
    p = urlparse((url or "").strip())
    q = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if k not in DROP_PARAMS]
    return urlunparse(p._replace(query=urlencode(q), fragment=""))


def same_site(url: str, host: str) -> bool:
    p = urlparse(url or "")
    return p.scheme in ("http", "https") and p.netloc.lower() == (host or "").lower()


def is_file_url(url: str) -> bool:
    return any(x in urlparse(url).path for x in FILE_PATHS)


def classify(url: str, host: str) -> str:
    """'go' — страница этого Moodle, 'file' — файл этого Moodle, 'ext' — другой сайт, '' — не показывать ссылкой."""
    p = urlparse(url)
    if p.scheme in ("mailto", "tel"):
        return "ext"
    if p.scheme not in ("http", "https"):
        return ""
    if p.netloc.lower() == (host or "").lower():
        if (any(x in p.path for x in SKIP_LINK_PATHS) or SKIP_QUERY_RX.search(p.query)
                or any(k in DROP_PARAMS for k, _ in parse_qsl(p.query))):
            return ""   # действия (ответить, сдать, начать попытку…) — только на сайте
        return "file" if is_file_url(url) else "go"
    return "ext"


def _clean_text(s) -> str:
    return re.sub(r"\s+", " ", htmllib.unescape(str(s or ""))).strip()


def render(html_text: str, page_url: str, host: str) -> dict:
    """HTML страницы Moodle → {"title", "html", "text", "forms": сколько форм (кнопок действий) убрано}."""
    soup = BeautifulSoup(html_text or "", PARSER)
    t = soup.select_one(TITLE_SEL)
    title = _clean_text(t.get_text(" ")) if t is not None else ""
    if not title and soup.title is not None:
        title = _clean_text(soup.title.get_text(" ")).split("|")[0].strip()
    main = None
    for sel in MAIN_SEL.split(", "):
        main = soup.select_one(sel)
        if main is not None:
            break
    main = copy.copy(main if main is not None else (soup.body or soup))
    wrap = BeautifulSoup("<div></div>", PARSER)
    root = wrap.div
    root.append(main)
    forms = len(root.find_all("form"))
    # заголовок страницы уже в шапке окна — убираем первый h1 в содержимом, чтобы не дублировать
    h1 = root.find("h1")
    if h1 is not None and title and _clean_text(h1.get_text(" ")) == title:
        h1.decompose()
    for el in root.select("iframe"):
        src = urljoin(page_url, el.get("src") or "")
        a = wrap.new_tag("a")
        a.string = "Встроенное содержимое: " + (_clean_text(el.get("title")) or urlparse(src).netloc or "открыть")
        a["href"] = src
        p = wrap.new_tag("p")
        p.append(a)
        el.replace_with(p)
    for el in root.select(STRIP_SEL):
        el.decompose()
    for el in list(root.find_all(True)):
        if el.parent is None:
            continue
        name = el.name
        if name not in ALLOWED:
            el.unwrap()
            continue
        href, src = el.get("href"), el.get("src")
        attrs = {k: v for k, v in el.attrs.items() if k in KEEP_ATTRS}
        el.attrs = attrs
        if name == "a":
            kind = classify(urljoin(page_url, href), host) if href and not href.startswith("#") and not href.lower().startswith("javascript:") else ""
            if not kind:
                el.unwrap()
                continue
            el["href"] = "#"
            el["data-r" + kind] = norm_url(urljoin(page_url, href)) if kind != "ext" else urljoin(page_url, href)
        elif name in ("img", "source", "video", "audio"):
            if name in ("video", "audio") and not src:
                continue
            if src:
                full = urljoin(page_url, src)
                if same_site(full, host):
                    el["data-src"] = norm_url(full)
                elif name == "img":
                    alt = _clean_text(el.get("alt"))
                    el.replace_with(f"[картинка с другого сайта{': ' + alt if alt else ''}]")
                    continue
                else:
                    el.decompose()
                    continue
            elif name == "img":
                el.decompose()
                continue
    for tag in root.find_all(["div", "span", "section", "article", "header", "aside", "p"]):
        if not tag.contents or (not tag.get_text(strip=True) and not tag.find(["img", "video", "audio", "table", "hr", "a"])):
            tag.decompose()
    body = root.decode_contents()
    body = re.sub(r"(\s*\n\s*){3,}", "\n\n", body).strip()
    text = _clean_text(root.get_text(" "))
    return {"title": title, "html": body, "text": text[:20000], "forms": forms}


# ── кэш прочитанных страниц (на диске, чтобы читалось и без интернета) ──
def _key(url: str) -> str:
    return hashlib.sha1(norm_url(url).encode("utf-8")).hexdigest()[:20]


def cache_dir(data_dir) -> Path:
    return Path(data_dir) / READER_DIR


def load(data_dir, url: str) -> dict | None:
    try:
        return json.loads((cache_dir(data_dir) / "pages" / (_key(url) + ".json")).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def save(data_dir, url: str, rec: dict) -> None:
    d = cache_dir(data_dir) / "pages"
    try:
        d.mkdir(parents=True, exist_ok=True)
        rec = dict(rec, url=norm_url(url), ts=time.time())
        tmp = d / (_key(url) + ".tmp")
        tmp.write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
        tmp.replace(d / (_key(url) + ".json"))
        files = sorted(d.glob("*.json"), key=lambda p: p.stat().st_mtime)
        for old in files[:max(0, len(files) - MAX_CACHE_PAGES)]:
            old.unlink(missing_ok=True)
    except OSError:
        pass


def image_path(data_dir, url: str) -> Path:
    return cache_dir(data_dir) / "files" / _key(url)


def prune_files(data_dir, limit_mb: int = 150) -> None:
    d = cache_dir(data_dir) / "files"
    try:
        files = sorted((p for p in d.iterdir() if p.is_file()), key=lambda p: p.stat().st_mtime)
        total = sum(p.stat().st_size for p in files)
        while files and total > limit_mb * 1024 * 1024:
            old = files.pop(0)
            total -= old.stat().st_size
            old.unlink(missing_ok=True)
    except OSError:
        pass
