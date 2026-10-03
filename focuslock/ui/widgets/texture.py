"""Grain tla: wiele rodzajow i poziomow ziarna (bez innych tekstur).

Wszystko, co robi ten modul, to **ziarno**: rozsypane punkty wybierane deter-
ministycznym szumem (64 poziomy krycia), powielane z malego, cache'owanego
kafelka (``drawTiledPixmap``). Dzieki temu:

* **rodzaje** - pojedyncze piksele (``sand``/``fine``/``medium``/``coarse``),
  grudki (``clump``, ``clump_coarse``, ``clump_rare`` - ziarno zlepione
  w kwadraty 2x2/3x3/4x4), rzadkie jasne pylki (``dust``) i ciemne ziarno na
  jasnych tlach (``pepper``),
* **poziomy** - od ``sand`` (ledwie widoczny piasek) do ``coarse`` (gesta
  kaszka); kazdy poziom ma inny kafelek i inne krycie, wiec nakladajac je
  dostajemy glebie bez widocznej siatki powtorzen,
* **koszt** - kafelek liczy sie raz, potem jest tylko powielany; liczba warstw
  nie wplywa na czas malowania,
* **monochromatycznosc** - kazdy piksel wyniku ma R == G == B (tokeny motywu).

Uwaga na przyszlosc: do ziarna **nie** uzywamy macierzy Bayera. Uporzadkowany
wzor ma (przy niskich kryciach) cale jasne i cale ciemne wiersze, co na duzym
tle czyta sie jak poziome pasy, a nie jak ziarno - dlatego maska jest szumem.

Z tego samego zestawu korzysta tlo okna, karty, puste stany, plakietki,
komunikaty, kafle aplikacji, pasek nawigacji, wykresy, kontrolki i dialogi.
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

    ``size`` to bok kafelka w pikselach (zawsze ``8 * block``), ``block`` -
    rozmiar grudki (1 = pojedyncze piksele, 2/3 = zlepione kwadraty),
    ``step`` - ile z 64 progow Bayera jest zapalonych, ``alpha`` - krycie
    ziarna, ``tint`` - kolor (``text`` = jasne ziarno, ``bg`` = ciemne).
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
    area = QRectF(rect)
    if area.isEmpty() or opacity <= 0.01:
        return
    width = max(1, int(ceil(area.width())))
    height = max(1, int(ceil(area.height())))
    image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    layer = QPainter(image)
    layer.drawTiledPixmap(QRect(0, 0, width, height), grain_pixmap(name))
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
    """KsztaĹ‚t clippingu: zaokraglony prostokat albo sam prostokat."""
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
) -> None:
    """Ziarno powierzchni (karta, panel, pigulka), przyciete do zaokraglenia.

    Nic nie wychodzi poza obramowanie widgetu malowane przez QSS.
    """
    area = QRectF(rect)
    if area.isEmpty():
        return
    paint_stack(painter, area, names, opacity=opacity, tint=tint, clip=clip_for(area, radius))


def paint_dialog_background(painter: QPainter, rect: QRectF, *, opacity: float = 0.9) -> None:
    """Tlo dialogu modalnego: ten sam stos ziarna, co tlo glownego okna.

    Dialogi nie stoja na tle okna (maja wlasna powierzchnie), wiec bez tego
    wypadaly z reszty interfejsu jako plaska czern.
    """
    area = QRectF(rect)
    if area.isEmpty():
        return
    painter.fillRect(area, qcolor("bg"))
    paint_stack(painter, area, BACKDROP_STACK, opacity=opacity)


def grains_report() -> str:
    """Krotki opis wszystkich rodzajow ziarna (diagnostyka, testy, raport CLI)."""
    return "; ".join(
        f"{item.name}: kafelek {item.size}px, grudka {item.block}px, "
        f"krycie {item.coverage:.0%}, alfa {item.alpha}"
        for item in GRAINS
    )


__all__ = [
    "BACKDROP_STACK",
    "GRAINS",
    "GRAINS_BY_NAME",
    "Grain",
    "LADDER",
    "MASK_LEVELS",
    "PHASE_STEP",
    "SOFT_STACK",
    "SURFACE_STACK",
    "clear_cache",
    "clip_for",
    "grain",
    "grain_pixmap",
    "grains_report",
    "ladder",
    "paint_dialog_background",
    "paint_grain",
    "paint_grain_fade",
    "paint_stack",
    "paint_surface",
    "rounded_path",
]
