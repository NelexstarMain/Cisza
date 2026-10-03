"""Faktura UI: ziarno (szum) i rastr (halftone) - rodzaje, poziomy, warstwy.

Modul ma dwie rodziny faktury, obie monochromatyczne i cache'owane:

**Ziarno** (``GRAINS``) - rozsypane punkty wybierane deterministycznym szumem
(64 poziomy krycia), powielane z malego kafelka:

* pojedyncze piksele: ``sand``/``fine``/``medium``/``coarse`` (od ledwo
  widocznego piasku do gestej kaszki),
* grudki: ``clump``/``clump_coarse``/``clump_rare`` (ziarno zlepione
  w kwadraty 2x2/3x3/4x4),
* ``dust`` (rzadkie jasne pylki) i ``pepper`` (ciemne ziarno na jasnych tlach).

**Rastr / halftone** (``HALFTONES``, ``SPARSE_SCREENS``) - kropki na stalej
siatce, jak na torze pierscienia i na wlaczniku:

* ``hair``/``veil``/``soft``/``light``/``mid``/``strong``/``dense``/``solid``
  oraz warianty ``coarse`` - od 3% do 100% krycia, w siatkach 2-6 px,
* ``HALFTONE_INTENSITIES`` - drabinka 64 stopni (``dot_side``),
* ``whisper``/``whisper_coarse`` - rzadkie, grube kropki tla (ledwo widoczne),
* ``paint_halftone_fade`` - rastr, ktory wygasa maska (plynna intensywnosc),
* ``dither_brush``/``fill_dither`` w ``paint`` - uporzadkowany rastr danych
  z 64 stopniami krycia (heatmapa, paski, wykresy, ikony).

Dzieki cache'owanym kafelkom liczba warstw nie podnosi kosztu malowania,
a kazdy piksel wyniku ma R == G == B (tokeny motywu).

Uwaga na przyszlosc: do *ziarna* nie uzywamy macierzy Bayera - uporzadkowany
wzor ma przy niskich kryciach cale jasne i cale ciemne wiersze, co na duzym
tle czyta sie jak poziome pasy. Do rastra Bayera uzywamy swiadomie (to wlasnie
daje rowny rastr kropek).

Z obu rodzin korzysta tlo okna, karty, puste stany, plakietki, komunikaty,
kafle aplikacji, pasek nawigacji, wykresy, wskazniki, kontrolki i dialogi.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import ceil

from PyQt6.QtCore import QPoint, QPointF, QRect, QRectF, Qt
from PyQt6.QtGui import QBrush, QImage, QLinearGradient, QPainter, QPainterPath, QPixmap

from .paint import qcolor

#: Liczba poziomow szumu (maska ma 64 stopnie krycia).
MASK_LEVELS = 64
_MASK = 0xFFFFFFFF


@dataclass(frozen=True)
class Grain:
    """Jeden rodzaj/poziom ziarna.

    ``size`` to bok kafelka w pikselach, ``block`` - rozmiar grudki
    (1 = pojedyncze piksele, 2/3/4 = zlepione kwadraty), ``step`` - ile z 64
    progow szumu jest zapalonych, ``alpha`` - krycie ziarna, ``tint`` - kolor
    (``text`` = jasne ziarno, ``bg`` = ciemne).
    """

    name: str
    size: int
    block: int
    step: int
    alpha: int
    tint: str = "text"

    @property
    def coverage(self) -> float:
        """Jaka czesc kafelka jest zadrukowana (0..1)."""
        return max(0.0, min(1.0, self.step / float(MASK_LEVELS)))

    @property
    def weight(self) -> float:
        """Srednia jasnosc warstwy (0..255) - do porownan i testow."""
        return self.coverage * float(self.alpha)


#: Rodzaje i poziomy ziarna - od najdrobniejszego do najgrubszego.
GRAINS: tuple[Grain, ...] = (
    Grain("sand", 24, 1, 4, 13),
    Grain("fine", 32, 1, 8, 12),
    Grain("medium", 48, 1, 16, 10),
    Grain("coarse", 64, 1, 30, 9),
    Grain("clump", 32, 2, 6, 11),
    Grain("clump_coarse", 48, 3, 4, 10),
    Grain("clump_rare", 64, 4, 1, 12),
    Grain("dust", 160, 1, 1, 24),
    Grain("pepper", 32, 1, 8, 15, "bg"),
)

GRAINS_BY_NAME: dict[str, Grain] = {item.name: item for item in GRAINS}

#: Nazwy poziomow od najdrobniejszego do najgrubszego (bez pylkow i pieprzu).
LADDER: tuple[str, ...] = ("sand", "fine", "medium", "coarse")
#: Domyslny stos tla okna: cztery poziomy + trzy rodzaje grudek.
BACKDROP_STACK: tuple[str, ...] = (
    "coarse",
    "medium",
    "fine",
    "sand",
    "clump",
    "clump_coarse",
    "clump_rare",
)
#: Domyslna faktura powierzchni (karty, panele, kafle).
SURFACE_STACK: tuple[str, ...] = ("fine", "sand", "clump")
#: Delikatna faktura drobnych elementow (plakietki, tory, pigulki).
SOFT_STACK: tuple[str, ...] = ("sand", "clump")

_PIXMAP_CACHE: dict[tuple[str, str, int], QPixmap] = {}
#: Przesuniecie fazy miedzy kolejnymi warstwami (px) - bez widocznej siatki.
PHASE_STEP = 13


def grain(name: str) -> Grain:
    """Poziom po nazwie; nieznana nazwa spada na ``fine`` (nigdy nie wywala UI)."""
    return GRAINS_BY_NAME.get(str(name), GRAINS_BY_NAME["fine"])


def ladder() -> tuple[Grain, ...]:
    """Cztery poziomy zwyklego ziarna (bez pylkow i pieprzu), od sand do coarse."""
    return tuple(GRAINS_BY_NAME[name] for name in LADDER)


def _noise(x: int, y: int, seed: int) -> int:
    """Deterministyczny szum 0..63 (mieszanka bitowa, bez losowosci globalnej).

    Zwraca wartosc niezalezna dla kazdej komorki kafelka - w przeciwienstwie do
    macierzy Bayera nie ma tu "jasnych" i "ciemnych" wierszy, wiec ziarno nie
    zamienia sie w poziome pasy.
    """
    value = (int(x) * 0x27D4EB2D) ^ (int(y) * 0x165667B1) ^ (int(seed) * 0x9E3779B1)
    value &= _MASK
    value ^= value >> 15
    value = (value * 0x2545F491) & _MASK
    value ^= value >> 13
    value = (value * 0x85EBCA6B) & _MASK
    return (value ^ (value >> 16)) & (MASK_LEVELS - 1)


def grain_pixmap(name: str = "fine", *, tint: str | None = None, alpha: float | None = None) -> QPixmap:
    """Kafelek ziarna (cache'owany po nazwie, kolorze i kryciu)."""
    spec = grain(name)
    tint_value = spec.tint if tint is None else str(tint)
    alpha_value = spec.alpha if alpha is None else max(0, min(255, int(round(float(alpha)))))
    key = (spec.name, tint_value, alpha_value)
    cached = _PIXMAP_CACHE.get(key)
    if cached is not None:
        return cached
    size = max(8, int(spec.size))
    block = max(1, int(spec.block))
    seed = sum(ord(ch) * (index + 1) for index, ch in enumerate(spec.name)) + 7
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    dot = qcolor(tint_value, alpha_value)
    step = max(0, min(MASK_LEVELS, int(spec.step)))
    # UWAGA: `drawPoint` z `NoPen` nie rysuje niczego (punkt bierze sie z piora),
    # dlatego kazde ziarno to jawny kwadracik `block x block` wypelniony pedzlem.
    for cell_y in range(size // block):
        for cell_x in range(size // block):
            if _noise(cell_x, cell_y, seed) < step:
                painter.fillRect(cell_x * block, cell_y * block, block, block, dot)
    painter.end()
    _PIXMAP_CACHE[key] = pixmap
    return pixmap


def clear_cache() -> None:
    """Czysci cache kafelkow (testy, zmiana motywu)."""
    _PIXMAP_CACHE.clear()
    _SCREEN_CACHE.clear()


# ------------------------------------------------------------------ halftone
@dataclass(frozen=True)
class Halftone:
    """Rastr (halftone): kropki na stalej siatce, wielkosc = intensywnosc.

    ``cell`` to odstep siatki w pikselach, ``level`` - ile z 64 stopni ma
    kropka (0 = brak, 64 = pelna kratka), ``alpha`` - krycie kropki. Kropki
    leza w rownych odstepach, wiec rastr czyta sie jak sitodruk / punktowane
    tlo znane z wykresow i pierscienia postepu.
    """

    name: str
    cell: int
    level: int
    alpha: int
    tint: str = "text"

    @property
    def coverage(self) -> float:
        """Jaka czesc kwadratu kratki zajmuje kropka (0..1)."""
        return max(0.0, min(1.0, self.level / float(MASK_LEVELS)))

    @property
    def dot_side(self) -> int:
        """Bok kropki w pikselach (1..cell)."""
        return dot_side(self.cell, self.level)


#: Rastry od najdelikatniejszego do najmocniejszego (rozne siatki i gestosci).
HALFTONES: tuple[Halftone, ...] = (
    Halftone("hair", 2, 2, 110),
    Halftone("veil", 3, 4, 105),
    Halftone("soft", 3, 9, 115),
    Halftone("light", 4, 14, 120),
    Halftone("mid", 4, 22, 130),
    Halftone("strong", 3, 34, 145),
    Halftone("dense", 2, 48, 155),
    Halftone("solid", 2, 64, 210),
    Halftone("coarse", 6, 9, 100),
    Halftone("coarse_mid", 6, 22, 110),
)

HALFTONE_BY_NAME: dict[str, Halftone] = {item.name: item for item in HALFTONES}

#: Kolejne intensywnosci rastra (0..64) - do gradientow, animacji i testow.
HALFTONE_INTENSITIES: tuple[int, ...] = (2, 4, 8, 12, 18, 26, 36, 48, 64)
#: Domyslny zestaw warstw rastra na powierzchni (drobny + gruby).
SCREEN_STACK: tuple[str, ...] = ("hair",)
#: Warstwy rastra na wiekszych powierzchniach (karta moze byc bogatsza).
SCREEN_STACK_RICH: tuple[str, ...] = ("veil", "coarse")
#: Najciensze warstwy tla: bardzo rzadkie, grube rastry (ledwo widoczne).
SCREEN_STACK_BACKDROP: tuple[str, ...] = ("whisper", "whisper_coarse")#: Krycie rastra na powierzchniach (karty, kafle) - kropka ma byc tlem, nie brokatem.
SCREEN_ALPHA_SURFACE = 38
#: Krycie rastra na tle okna i na pasku nawigacji.
SCREEN_ALPHA_BACKDROP = 55
#: Krycie rastra w danych (wykresy, heatmapa) - tam rastr jest trescia.
SCREEN_ALPHA_DATA = 90

_SCREEN_CACHE: dict[tuple[int, int, str, int], QPixmap] = {}


def dot_side(cell: int, level: int) -> int:
    """Bok kropki rastra dla ``level`` z 64 stopni (1..cell)."""
    size = max(1, int(cell))
    step = max(0, min(MASK_LEVELS, int(level)))
    if step <= 0:
        return 0
    return max(1, min(size, int(round(size * (step / float(MASK_LEVELS)) ** 0.5))))


def halftone(name: str) -> Halftone:
    """Rastr po nazwie; nieznana nazwa spada na ``soft``."""
    return HALFTONE_BY_NAME.get(str(name), HALFTONE_BY_NAME["soft"])


def halftone_pixmap(
    name: str | None = None,
    *,
    cell: int | None = None,
    level: int | None = None,
    alpha: int | None = None,
    tint: str | None = None,
) -> QPixmap:
    """Kafelek rastra: jedna kropka na kratke ``cell x cell`` (cache'owany)."""
    spec = halftone(name or "soft")
    cell_value = max(2, int(cell if cell is not None else spec.cell))
    level_value = max(0, min(MASK_LEVELS, int(level if level is not None else spec.level)))
    alpha_value = max(0, min(255, int(alpha if alpha is not None else spec.alpha)))
    tint_value = str(tint if tint is not None else spec.tint)
    key = (cell_value, level_value, tint_value, alpha_value)
    cached = _SCREEN_CACHE.get(key)
    if cached is not None:
        return cached
    pixmap = QPixmap(cell_value, cell_value)
    pixmap.fill(Qt.GlobalColor.transparent)
    side = dot_side(cell_value, level_value)
    if side > 0 and alpha_value > 0:
        offset = (cell_value - side) // 2
        painter = QPainter(pixmap)
        painter.fillRect(offset, offset, side, side, qcolor(tint_value, alpha_value))
        painter.end()
    _SCREEN_CACHE[key] = pixmap
    return pixmap


def paint_halftone(
    painter: QPainter,
    rect: QRectF,
    name: str = "hair",
    *,
    opacity: float = 1.0,
    phase: int = 0,
    tint: str | None = None,
    cell: int | None = None,
    level: int | None = None,
    alpha: int | None = None,
    clip: QPainterPath | QRectF | None = None,
) -> None:
    """Powiela rastr na ``rect`` (kropki w rownych odstepach)."""
    area = QRectF(rect)
    if area.isEmpty() or opacity <= 0.01:
        return
    painter.save()
    _apply_clip(painter, area, clip)
    painter.setOpacity(max(0.0, min(1.0, float(opacity))))
    tile = halftone_pixmap(name, cell=cell, level=level, alpha=alpha, tint=tint)
    offset = QPoint(int(phase) % max(1, tile.width()), int(phase) % max(1, tile.height()))
    painter.drawTiledPixmap(area.toRect(), tile, offset)
    painter.restore()


# ------------------------------------------------------------- rzadki rastr
#: Rzadkie, grube rastry tla: ``(bok kropki px, pozycje z 64, alfa)``.
#: Kropka stoi tylko na 1 z 64 pozycji siatki, wiec na ekranie widac pojedyncze
#: punktowania rozstawione co kilkadziesiat pikseli - ledwo widoczna warstwa.
SPARSE_SCREENS: dict[str, tuple[int, int, int]] = {
    "whisper": (4, 1, 18),
    "whisper_coarse": (8, 1, 14),
}
#: Kolejnosc warstw rzadkiego rastra na tle okna.
SCREEN_STACK_BACKDROP: tuple[str, ...] = ("whisper", "whisper_coarse")


def sparse_pixmap(
    name: str = "whisper",
    *,
    tint: str | None = None,
    alpha: int | None = None,
) -> QPixmap:
    """Kafelek rzadkiego rastra: osiem pozycji na bok, zapalone wg Bayera 8x8."""
    dot, level, alpha_default = SPARSE_SCREENS.get(str(name), SPARSE_SCREENS["whisper"])
    dot_value = max(2, int(dot))
    step = max(0, min(MASK_LEVELS, int(level)))
    alpha_value = max(0, min(255, int(alpha if alpha is not None else alpha_default)))
    tint_value = str(tint or "text")
    size = dot_value * 8
    key = (size, step, tint_value, alpha_value)
    cached = _SCREEN_CACHE.get(key)
    if cached is not None:
        return cached
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    if step > 0 and alpha_value > 0:
        from .paint import BAYER8

        painter = QPainter(pixmap)
        dot_color = qcolor(tint_value, alpha_value)
        for by in range(8):
            row = BAYER8[by]
            for bx in range(8):
                if row[bx] < step:
                    painter.fillRect(bx * dot_value, by * dot_value, dot_value, dot_value, dot_color)
        painter.end()
    _SCREEN_CACHE[key] = pixmap
    return pixmap


def paint_sparse(
    painter: QPainter,
    rect: QRectF,
    name: str = "whisper",
    *,
    opacity: float = 1.0,
    phase: int = 0,
    tint: str | None = None,
    alpha: int | None = None,
    clip: QPainterPath | QRectF | None = None,
) -> None:
    """Powiela rzadki rastr na ``rect`` (pojedyncze kropki co kilkadziesiat px)."""
    area = QRectF(rect)
    if area.isEmpty() or opacity <= 0.01:
        return
    painter.save()
    _apply_clip(painter, area, clip)
    painter.setOpacity(max(0.0, min(1.0, float(opacity))))
    tile = sparse_pixmap(name, tint=tint, alpha=alpha)
    offset = QPoint(int(phase) % max(1, tile.width()), int(phase * 3) % max(1, tile.height()))
    painter.drawTiledPixmap(area.toRect(), tile, offset)
    painter.restore()


def paint_sparse_stack(
    painter: QPainter,
    rect: QRectF,
    names: tuple[str, ...] | list[str] = SCREEN_STACK_BACKDROP,
    *,
    opacity: float = 1.0,
    tint: str | None = None,
    alpha: int | None = None,
    clip: QPainterPath | QRectF | None = None,
    phase_shift: int = 7,
) -> None:
    """Naklada kilka rzadkich rastrow (rozne kropki i rozne rozstawy)."""
    for index, name in enumerate(names):
        paint_sparse(
            painter,
            rect,
            name,
            opacity=opacity,
            phase=index * int(phase_shift),
            tint=tint,
            alpha=alpha,
            clip=clip,
        )


def paint_screen_stack(
    painter: QPainter,
    rect: QRectF,
    names: tuple[str, ...] | list[str] = SCREEN_STACK,
    *,
    opacity: float = 1.0,
    tint: str | None = None,
    alpha: int | None = None,
    clip: QPainterPath | QRectF | None = None,
    phase_shift: int = 3,
) -> None:
    """Naklada kilka rastrow o roznych siatkach (wielowarstwowa faktura)."""
    for index, name in enumerate(names):
        paint_halftone(
            painter,
            rect,
            name,
            opacity=opacity,
            phase=index * int(phase_shift),
            tint=tint,
            alpha=alpha,
            clip=clip,
        )


def _apply_clip(painter: QPainter, rect: QRectF, clip: QPainterPath | QRectF | None) -> None:
    """Ustawia clipping na ``rect`` (i opcjonalnie na dodatkowy ksztalt)."""
    painter.setClipRect(rect)
    if clip is None:
        return
    if isinstance(clip, QPainterPath):
        painter.setClipPath(clip, Qt.ClipOperation.IntersectClip)
    else:
        painter.setClipRect(QRectF(clip), Qt.ClipOperation.IntersectClip)


def paint_grain(
    painter: QPainter,
    rect: QRectF,
    name: str = "fine",
    *,
    phase: int = 0,
    opacity: float = 1.0,
    tint: str | None = None,
    clip: QPainterPath | QRectF | None = None,
) -> None:
    """Powiela kafelek jednego poziomu ziarna na ``rect`` (z faza ``phase``)."""
    area = QRectF(rect)
    if area.isEmpty() or opacity <= 0.01:
        return
    painter.save()
    _apply_clip(painter, area, clip)
    painter.setOpacity(max(0.0, min(1.0, float(opacity))))
    tile = grain_pixmap(name, tint=tint)
    offset = QPoint(int(phase) % max(1, tile.width()), int(phase * 2) % max(1, tile.height()))
    painter.drawTiledPixmap(area.toRect(), tile, offset)
    painter.restore()


def paint_stack(
    painter: QPainter,
    rect: QRectF,
    names: tuple[str, ...] | list[str] = SURFACE_STACK,
    *,
    opacity: float = 1.0,
    tint: str | None = None,
    clip: QPainterPath | QRectF | None = None,
    phase_shift: int = PHASE_STEP,
) -> None:
    """Naklada kilka poziomow ziarna jeden na drugi (kazdy z wlasna faza)."""
    for index, name in enumerate(names):
        paint_grain(
            painter,
            rect,
            name,
            phase=index * int(phase_shift),
            opacity=opacity,
            tint=tint,
            clip=clip,
        )


def _fade_gradient(rect: QRectF, fade: str, span: float) -> QLinearGradient:
    """Gradient krycia dla :func:`paint_grain_fade`.

    ``fade`` to kierunek, w ktorym ziarno WYGASA: ``"bottom"`` = mocne u gory
    i gasnie w dol, ``"right"`` = mocne przy lewej krawedzi i gasnie w prawo.
    """
    span = max(0.05, min(1.0, float(span)))
    if fade == "bottom":
        start, end = QPointF(rect.left(), rect.top()), QPointF(rect.left(), rect.top() + rect.height() * span)
    elif fade == "top":
        start, end = QPointF(rect.left(), rect.bottom()), QPointF(rect.left(), rect.bottom() - rect.height() * span)
    elif fade == "right":
        start, end = QPointF(rect.left(), rect.top()), QPointF(rect.left() + rect.width() * span, rect.top())
    elif fade == "left":
        start, end = QPointF(rect.right(), rect.top()), QPointF(rect.right() - rect.width() * span, rect.top())
    else:
        start, end = QPointF(rect.left(), rect.top()), QPointF(rect.left(), rect.top() + rect.height() * span)
    gradient = QLinearGradient(start, end)
    gradient.setColorAt(0.0, qcolor("text", 255))
    gradient.setColorAt(1.0, qcolor("text", 0))
    return gradient


def paint_grain_fade(
    painter: QPainter,
    rect: QRectF,
    name: str = "fine",
    *,
    fade: str = "bottom",
    span: float = 0.6,
    opacity: float = 1.0,
    clip: QPainterPath | QRectF | None = None,
) -> None:
    """Ziarno z maska: krycie wygasa w jedna strone (bez widocznego progu).

    Ziarno rysowane jest na malym obrazie (tylko na ``rect``), a potem maskowane
    gradientem - dzieki temu warstwa konczy sie plynnie i nie zostawia linii,
    jakiej nie da sie uniknac, przycinajac kafelek do polowy widgetu.
    """
    _paint_tile_fade(painter, rect, grain_pixmap(name), fade=fade, span=span, opacity=opacity, clip=clip)


def paint_halftone_fade(
    painter: QPainter,
    rect: QRectF,
    name: str = "soft",
    *,
    fade: str = "bottom",
    span: float = 0.6,
    opacity: float = 1.0,
    cell: int | None = None,
    level: int | None = None,
    alpha: int | None = None,
    tint: str | None = None,
    clip: QPainterPath | QRectF | None = None,
) -> None:
    """Rastr z maska: kropki pojawiaja sie stopniowo (płynna intensywność).

    To odpowiedz na "wiecej roznych intensywnosci": zamiast kilku stalych
    gestosci rastra, jedna warstwa przechodzi plynnie od kropek pelnych do
    zera (albo odwrotnie).
    """
    _paint_tile_fade(
        painter,
        rect,
        halftone_pixmap(name, cell=cell, level=level, alpha=alpha, tint=tint),
        fade=fade,
        span=span,
        opacity=opacity,
        clip=clip,
    )


def _paint_tile_fade(
    painter: QPainter,
    rect: QRectF,
    tile: QPixmap,
    *,
    fade: str,
    span: float,
    opacity: float,
    clip: QPainterPath | QRectF | None,
) -> None:
    """Wspolny mechanizm wygaszania kafelka (ziarno i rastr)."""
    area = QRectF(rect)
    if area.isEmpty() or opacity <= 0.01 or tile.isNull():
        return
    width = max(1, int(ceil(area.width())))
    height = max(1, int(ceil(area.height())))
    image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    layer = QPainter(image)
    layer.drawTiledPixmap(QRect(0, 0, width, height), tile)
    layer.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
    layer.setPen(Qt.PenStyle.NoPen)
    layer.setBrush(QBrush(_fade_gradient(QRectF(0, 0, width, height), fade, span)))
    layer.drawRect(0, 0, width, height)
    layer.end()
    painter.save()
    _apply_clip(painter, area, clip)
    painter.setOpacity(max(0.0, min(1.0, float(opacity))))
    painter.drawImage(area.topLeft(), image)
    painter.restore()


def rounded_path(rect: QRectF, radius: float) -> QPainterPath:
    """Sciezka prostokata z zaokraglonymi naroznikami (do clippingu ziarna)."""
    path = QPainterPath()
    path.addRoundedRect(QRectF(rect), max(0.0, float(radius)), max(0.0, float(radius)))
    return path


def clip_for(rect: QRectF, radius: float, *, inset: float = 0.0) -> QPainterPath | QRectF:
    """Ksztalt clippingu: zaokraglony prostokat albo sam prostokat."""
    area = QRectF(rect).adjusted(inset, inset, -inset, -inset)
    if radius > 0.0:
        return rounded_path(area, radius)
    return area


def paint_surface(
    painter: QPainter,
    rect: QRectF,
    *,
    radius: float = 0.0,
    names: tuple[str, ...] | list[str] = SURFACE_STACK,
    opacity: float = 0.7,
    tint: str | None = None,
    screens: tuple[str, ...] | list[str] = SCREEN_STACK,
    screen_opacity: float = 0.85,
    screen_tint: str | None = None,
    screen_alpha: int | None = SCREEN_ALPHA_SURFACE,
) -> None:
    """Ziarno + rastr powierzchni (karta, panel, pigulka), przyciete do zaokraglenia.

    Kazda powierzchnia jest wielowarstwowa: najpierw ziarno (szum), a na nim
    jeden lub dwa rastry o roznych siatkach. Nic nie wychodzi poza obramowanie
    widgetu malowane przez QSS.
    """
    area = QRectF(rect)
    if area.isEmpty():
        return
    clip = clip_for(area, radius)
    paint_stack(painter, area, names, opacity=opacity, tint=tint, clip=clip)
    if screens:
        paint_screen_stack(
            painter,
            area,
            screens,
            opacity=screen_opacity,
            tint=screen_tint,
            alpha=screen_alpha,
            clip=clip,
        )


def paint_dialog_background(
    painter: QPainter,
    rect: QRectF,
    *,
    opacity: float = 0.9,
    screens: tuple[str, ...] | list[str] = SCREEN_STACK_RICH,
    screen_opacity: float = 0.8,
    screen_alpha: int | None = SCREEN_ALPHA_BACKDROP,
) -> None:
    """Tlo dialogu modalnego: ziarno + rastr, jak tlo glownego okna.

    Dialogi nie stoja na tle okna (maja wlasna powierzchnie), wiec bez tego
    wypadaly z reszty interfejsu jako plaska czern.
    """
    area = QRectF(rect)
    if area.isEmpty():
        return
    painter.fillRect(area, qcolor("bg"))
    paint_stack(painter, area, BACKDROP_STACK, opacity=opacity)
    if screens:
        paint_screen_stack(painter, area, screens, opacity=screen_opacity, alpha=screen_alpha)
    paint_grain(painter, area, "dust", phase=9, opacity=0.8)


def grains_report() -> str:
    """Krotki opis wszystkich rodzajow ziarna (diagnostyka, testy, raport CLI)."""
    return "; ".join(
        f"{item.name}: kafelek {item.size}px, grudka {item.block}px, "
        f"krycie {item.coverage:.0%}, alfa {item.alpha}"
        for item in GRAINS
    )


def halftones_report() -> str:
    """Krotki opis rastrow (diagnostyka, testy, raport CLI)."""
    return "; ".join(
        f"{item.name}: siatka {item.cell}px, kropka {item.dot_side}px "
        f"({item.coverage:.0%}), alfa {item.alpha}"
        for item in HALFTONES
    )


__all__ = [
    "BACKDROP_STACK",
    "GRAINS",
    "GRAINS_BY_NAME",
    "Grain",
    "HALFTONES",
    "HALFTONE_BY_NAME",
    "HALFTONE_INTENSITIES",
    "Halftone",
    "LADDER",
    "MASK_LEVELS",
    "PHASE_STEP",
    "SCREEN_ALPHA_BACKDROP",
    "SCREEN_ALPHA_DATA",
    "SCREEN_ALPHA_SURFACE",
    "SCREEN_STACK",
    "SCREEN_STACK_BACKDROP",
    "SCREEN_STACK_RICH",
    "SOFT_STACK",
    "SPARSE_SCREENS",
    "SURFACE_STACK",
    "clear_cache",
    "clip_for",
    "dot_side",
    "grain",
    "grain_pixmap",
    "grains_report",
    "halftone",
    "halftone_pixmap",
    "halftones_report",
    "ladder",
    "paint_dialog_background",
    "paint_grain",
    "paint_grain_fade",
    "paint_halftone",
    "paint_halftone_fade",
    "paint_screen_stack",
    "paint_sparse",
    "paint_sparse_stack",
    "paint_stack",
    "paint_surface",
    "rounded_path",
    "sparse_pixmap",
]
