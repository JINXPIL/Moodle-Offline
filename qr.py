# -*- coding: utf-8 -*-
"""
Мини-генератор QR-кода (часть Moodle Offline), без сторонних библиотек.

Нужен для переноса данных по Wi-Fi: компьютер показывает QR с адресом, телефон наводит камеру.
Умеет: байтовый режим (UTF-8), версии 1–10, уровни исправления ошибок L и M, все 8 масок с выбором лучшей —
этого хватает на адрес до ~120 байт. Результат — матрица `list[list[bool]]` (True = тёмный модуль) и SVG.
"""
from __future__ import annotations

# (всего кодовых слов, [(блоков, слов данных в блоке)], EC-слов на блок) — таблица из стандарта ISO/IEC 18004
_BLOCKS = {
    "L": {1: (26, [(1, 19)], 7), 2: (44, [(1, 34)], 10), 3: (70, [(1, 55)], 15), 4: (100, [(1, 80)], 20),
          5: (134, [(1, 108)], 26), 6: (172, [(2, 68)], 18), 7: (196, [(2, 78)], 20), 8: (242, [(2, 97)], 24),
          9: (292, [(2, 116)], 30), 10: (346, [(2, 68), (2, 69)], 18)},
    "M": {1: (26, [(1, 16)], 10), 2: (44, [(1, 28)], 16), 3: (70, [(1, 44)], 26), 4: (100, [(2, 32)], 18),
          5: (134, [(2, 43)], 24), 6: (172, [(4, 27)], 16), 7: (196, [(4, 31)], 18), 8: (242, [(2, 38), (2, 39)], 22),
          9: (292, [(3, 36), (2, 37)], 22), 10: (346, [(4, 43), (1, 44)], 26)},
}
_ALIGN = {1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30], 6: [6, 34], 7: [6, 22, 38], 8: [6, 24, 42],
          9: [6, 26, 46], 10: [6, 28, 50]}
_FMT_BITS = {"L": 1, "M": 0}

# поле Галуа GF(256), многочлен 0x11D
_EXP, _LOG = [0] * 512, [0] * 256
_x = 1
for _i in range(255):
    _EXP[_i] = _x
    _LOG[_x] = _i
    _x <<= 1
    if _x & 0x100:
        _x ^= 0x11D
for _i in range(255, 512):
    _EXP[_i] = _EXP[_i - 255]


def _gmul(a: int, b: int) -> int:
    return 0 if not a or not b else _EXP[_LOG[a] + _LOG[b]]


def _rs_generator(n: int) -> list:
    g = [1]
    for i in range(n):
        nxt = [0] * (len(g) + 1)
        for j, c in enumerate(g):
            nxt[j] ^= c
            nxt[j + 1] ^= _gmul(c, _EXP[i])
        g = nxt
    return g


def _rs_remainder(data: list, n: int) -> list:
    gen = _rs_generator(n)
    rem = [0] * n
    for b in data:
        f = b ^ rem[0]
        rem = rem[1:] + [0]
        if f:
            for i in range(n):
                rem[i] ^= _gmul(gen[i + 1], f)
    return rem


def _capacity(version: int, level: str) -> int:
    total, blocks, ec = _BLOCKS[level][version]
    data_words = sum(n * d for n, d in blocks)
    return data_words - 2 if version < 10 else data_words - 3      # режим (4 бита) + длина (8 или 16 бит)


def _bits(value: int, n: int) -> list:
    return [(value >> (n - 1 - i)) & 1 for i in range(n)]


def _encode_data(data: bytes, version: int, level: str) -> list:
    total, blocks, ec = _BLOCKS[level][version]
    cap_words = sum(n * d for n, d in blocks)
    bits = _bits(0b0100, 4) + _bits(len(data), 8 if version < 10 else 16)
    for b in data:
        bits += _bits(b, 8)
    limit = cap_words * 8
    bits += [0] * min(4, limit - len(bits))
    bits += [0] * (-len(bits) % 8)
    words = [int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8)]
    pad = (0xEC, 0x11)
    i = 0
    while len(words) < cap_words:
        words.append(pad[i % 2])
        i += 1
    # блоки данных + EC, затем перемежение
    parts, pos = [], 0
    for n, d in blocks:
        for _ in range(n):
            parts.append(words[pos:pos + d])
            pos += d
    ecs = [_rs_remainder(p, ec) for p in parts]
    out = []
    for i in range(max(len(p) for p in parts)):
        for p in parts:
            if i < len(p):
                out.append(p[i])
    for i in range(ec):
        for e in ecs:
            out.append(e[i])
    return out


def _bch(value: int, poly: int, deg: int, n_bits: int) -> int:
    v = value << deg
    for i in range(n_bits - 1, deg - 1, -1):
        if (v >> i) & 1:
            v ^= poly << (i - deg)
    return v


def _format_bits(level: str, mask: int) -> int:
    data = (_FMT_BITS[level] << 3) | mask
    return ((data << 10) | _bch(data, 0x537, 10, 15)) ^ 0x5412


def _version_bits(version: int) -> int:
    return (version << 12) | _bch(version, 0x1F25, 12, 18)


class _Matrix:
    def __init__(self, version: int):
        self.n = 17 + 4 * version
        self.v = version
        self.m = [[False] * self.n for _ in range(self.n)]
        self.func = [[False] * self.n for _ in range(self.n)]
        self._patterns()

    def _set(self, r, c, val, func=True):
        if 0 <= r < self.n and 0 <= c < self.n:
            self.m[r][c] = val
            if func:
                self.func[r][c] = True

    def _finder(self, r0, c0):
        for dr in range(-1, 8):
            for dc in range(-1, 8):
                r, c = r0 + dr, c0 + dc
                if not (0 <= r < self.n and 0 <= c < self.n):
                    continue
                inside = 0 <= dr <= 6 and 0 <= dc <= 6
                dark = inside and (dr in (0, 6) or dc in (0, 6) or (2 <= dr <= 4 and 2 <= dc <= 4))
                self._set(r, c, dark)

    def _patterns(self):
        n = self.n
        self._finder(0, 0)
        self._finder(0, n - 7)
        self._finder(n - 7, 0)
        for i in range(8, n - 8):                       # линии синхронизации
            self._set(6, i, i % 2 == 0)
            self._set(i, 6, i % 2 == 0)
        pts = _ALIGN[self.v]
        for r in pts:
            for c in pts:
                if (r == 6 and c == 6) or (r == 6 and c == pts[-1]) or (r == pts[-1] and c == 6):
                    continue
                for dr in range(-2, 3):
                    for dc in range(-2, 3):
                        self._set(r + dr, c + dc, max(abs(dr), abs(dc)) != 1)
        self._set(n - 8, 8, True)                       # «тёмный модуль»
        for i in range(9):                              # место под формат
            self.func[8][i] = self.func[i][8] = True
        for i in range(8):
            self.func[8][n - 1 - i] = self.func[n - 1 - i][8] = True
        if self.v >= 7:                                 # место под версию
            vb = _version_bits(self.v)
            for i in range(18):
                bit = bool((vb >> i) & 1)
                a, b = i // 3, i % 3
                self._set(a, n - 11 + b, bit)
                self._set(n - 11 + b, a, bit)

    def place(self, codewords: list):
        bits = []
        for w in codewords:
            bits += _bits(w, 8)
        n, i = self.n, 0
        col = n - 1
        up = True
        while col > 0:
            if col == 6:
                col -= 1
            rows = range(n - 1, -1, -1) if up else range(n)
            for r in rows:
                for c in (col, col - 1):
                    if not self.func[r][c]:
                        self.m[r][c] = bool(bits[i]) if i < len(bits) else False
                        i += 1
            up = not up
            col -= 2

    def apply_mask(self, mask: int):
        f = _MASKS[mask]
        for r in range(self.n):
            for c in range(self.n):
                if not self.func[r][c] and f(r, c):
                    self.m[r][c] = not self.m[r][c]

    def write_format(self, level: str, mask: int):
        fb = _format_bits(level, mask)
        n = self.n
        for i in range(15):
            bit = bool((fb >> i) & 1)
            # первая копия — вокруг левого верхнего «глаза»
            if i < 6:
                self.m[i][8] = bit
            elif i < 8:
                self.m[i + 1][8] = bit
            elif i == 8:
                self.m[8][7] = bit
            else:
                self.m[8][14 - i] = bit
            # вторая копия — у двух других
            if i < 8:
                self.m[8][n - 1 - i] = bit
            else:
                self.m[n - 15 + i][8] = bit
        self.m[n - 8][8] = True

    def penalty(self) -> int:
        m, n, score = self.m, self.n, 0
        for grid in (m, [list(col) for col in zip(*m)]):       # строки и столбцы
            for row in grid:
                run, prev = 1, row[0]
                for x in row[1:]:
                    if x == prev:
                        run += 1
                    else:
                        if run >= 5:
                            score += run - 2
                        run, prev = 1, x
                if run >= 5:
                    score += run - 2
        for r in range(n - 1):                                  # блоки 2×2
            for c in range(n - 1):
                if m[r][c] == m[r][c + 1] == m[r + 1][c] == m[r + 1][c + 1]:
                    score += 3
        pat1 = [True, False, True, True, True, False, True, False, False, False, False]
        pat2 = pat1[::-1]
        for grid in (m, [list(col) for col in zip(*m)]):        # «глазоподобные» узоры
            for row in grid:
                for i in range(n - 10):
                    seg = row[i:i + 11]
                    if seg == pat1 or seg == pat2:
                        score += 40
        dark = sum(sum(r) for r in m)
        score += (abs(dark * 20 - n * n * 10) // (n * n)) * 10
        return score


_MASKS = [
    lambda r, c: (r + c) % 2 == 0,
    lambda r, c: r % 2 == 0,
    lambda r, c: c % 3 == 0,
    lambda r, c: (r + c) % 3 == 0,
    lambda r, c: (r // 2 + c // 3) % 2 == 0,
    lambda r, c: (r * c) % 2 + (r * c) % 3 == 0,
    lambda r, c: ((r * c) % 2 + (r * c) % 3) % 2 == 0,
    lambda r, c: ((r + c) % 2 + (r * c) % 3) % 2 == 0,
]


def make(text: str, level: str = "M") -> list:
    """Матрица QR-кода для строки (True — тёмный модуль). Слишком длинный текст → ValueError."""
    data = text.encode("utf-8")
    for level_try in ([level] if level == "L" else [level, "L"]):
        for version in range(1, 11):
            if len(data) <= _capacity(version, level_try):
                break
        else:
            continue
        break
    else:
        raise ValueError("текст слишком длинный для QR-кода")
    level = level_try
    words = _encode_data(data, version, level)
    best = None
    for mask in range(8):
        mx = _Matrix(version)
        mx.place(words)
        mx.apply_mask(mask)
        mx.write_format(level, mask)
        p = mx.penalty()
        if best is None or p < best[0]:
            best = (p, mx.m)
    return best[1]


def to_svg(matrix: list, border: int = 4, dark: str = "#000", light: str = "#fff") -> str:
    """SVG с квадратами-модулями: чёрное на белом (с белой рамкой) — так камеры читают надёжнее всего."""
    n = len(matrix)
    size = n + 2 * border
    rects = []
    for r, row in enumerate(matrix):
        c = 0
        while c < n:                                    # соседние тёмные модули одной строки — одним прямоугольником
            if row[c]:
                start = c
                while c < n and row[c]:
                    c += 1
                rects.append(f"M{start + border},{r + border}h{c - start}v1h-{c - start}z")
            else:
                c += 1
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}" shape-rendering="crispEdges">'
            f'<rect width="{size}" height="{size}" fill="{light}"/><path d="{"".join(rects)}" fill="{dark}"/></svg>')
