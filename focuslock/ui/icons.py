"""Ikony aplikacji: ekstrakcja z `.lnk`/`.exe`/`.ico` + odbarwianie do szarosci.

Warstwa czysto prezentacyjna. Kazda ikone mozna zwrocic w wersji kolorowej
(ustawienie `ui.color_app_icons`) albo odbarwionej — z zachowaniem
przezroczystosci. Gdy nie da sie wyciagnac ikony, rysujemy monogram
(pierwsza litera nazwy w obramowanym kwadracie) wylacznie z tokenow motywu.
"""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QFileInfo, QRectF, QSize, Qt
from PyQt6.QtGui import QColor, QFont, QImage, QPainter, QPixmap

try:  # Qt6 trzyma QFileIconProvider w QtWidgets (w Qt5 bylo w QtGui)
    from PyQt6.QtWidgets import QFileIconProvider
except ImportError:  # pragma: no cover - zalezy od wersji PyQt6
    from PyQt6.QtGui import QFileIconProvider  # type: ignore[attr-defined]

from .theme import TYPO
from .widgets.paint import dither_brush, pen, qcolor

# Pliki graficzne (ikony aplikacji ze Sklepu) czytamy wprost — powloka Windows
# zwraca dla nich jeden generyczny glif, wiec QFileIconProvider tu nie pomaga.
IMAGE_SUFFIXES: tuple[str, ...] = (".png", ".ico", ".jpg", ".jpeg", ".bmp", ".webp", ".tif", ".tiff", ".gif")


def desaturate(pixmap: QPixmap) -> QPixmap:
    """Zamienia ikone na skale szarosci (R == G == B), zachowujac kanal alfa."""
    if pixmap.isNull():
        return pixmap
    source = pixmap.toImage()
    if source.isNull():
        return pixmap
    gray = source.convertToFormat(QImage.Format.Format_Grayscale8).convertToFormat(
        QImage.Format.Format_RGB32
    )
    result = QImage(source.size(), QImage.Format.Format_ARGB32_Premultiplied)
    result.fill(QColor(0, 0, 0, 0))
    painter = QPainter(result)
    painter.drawImage(0, 0, gray)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
    painter.drawImage(0, 0, source.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied))
    painter.end()
    return QPixmap.fromImage(result)


class IconCache:
    """Cache ikon po `(sciezka, rozmiar, gray)`; zapasowo monogram.

    Kolejnosc zrodel dla kazdej sciezki: plik graficzny (`QPixmap`) -> powloka
    (`QFileIconProvider`) -> monogram. Dzieki temu brak uprawnien do odczytu
    pliku z `WindowsApps` nie konczy sie od razu litera w kwadracie.
    """

    def __init__(self) -> None:
        self._cache: dict[tuple[str, int, bool], QPixmap] = {}
        self._provider: QFileIconProvider | None = None
        self._counters: dict[str, int] = {"image": 0, "shell": 0, "monogram": 0}
        self._monogram_paths: list[str] = []

    # ------------------------------------------------------------------ API
    def pixmap(self, icon_path: str, size: int = 40, gray: bool = True) -> QPixmap:
        """Ikona dla pliku; nigdy nie zwraca pustego QPixmap."""
        path = str(icon_path or "")
        size = max(8, int(size))
        key = (path.lower(), size, bool(gray))
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        pixmap, source = self._resolve(path, size)
        if pixmap.isNull():
            pixmap = self.monogram(Path(path).stem if path else "", size)
            source = "monogram"
        elif gray:
            pixmap = desaturate(pixmap)
        self._count(source, path)
        self._cache[key] = pixmap
        return pixmap

    def monogram(self, name: str, size: int = 40) -> QPixmap:
        """Zapasowa ikona: pierwsza litera nazwy w obramowanym kwadracie."""
        size = max(8, int(size))
        pixmap = QPixmap(size, size)
        pixmap.fill(QColor(0, 0, 0, 0))
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(0.5, 0.5, size - 1.0, size - 1.0)
        radius = size * 0.2
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(qcolor("surface2"))
        painter.drawRoundedRect(rect, radius, radius)
        if size >= 16:
            inner = rect.adjusted(3.0, 3.0, -3.0, -3.0)
            painter.setBrush(dither_brush("text", 0.12, 4, bg="surface2"))
            painter.drawRoundedRect(inner, radius * 0.7, radius * 0.7)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(pen("line_strong", 1.0))
        painter.drawRoundedRect(rect, radius, radius)

        letter = (str(name or "").strip()[:1] or "?").upper()
        font = QFont(TYPO.mono_family)
        font.setStyleHint(QFont.StyleHint.Monospace)
        font.setPixelSize(max(8, int(size * 0.46)))
        font.setWeight(QFont.Weight.Medium)
        painter.setFont(font)
        painter.setPen(qcolor("text_dim"))
        painter.drawText(rect, int(Qt.AlignmentFlag.AlignCenter), letter)
        painter.end()
        return pixmap

    def clear_cache(self) -> None:
        self._cache.clear()
        self._counters = {"image": 0, "shell": 0, "monogram": 0}
        self._monogram_paths.clear()

    def stats(self) -> dict:
        """Diagnostyka: ile ikon z pliku, ile z powloki, ile monogramow."""
        return {
            "entries": len(self._cache),
            "image": self._counters.get("image", 0),
            "shell": self._counters.get("shell", 0),
            "monogram": self._counters.get("monogram", 0),
            "monogram_paths": sorted(set(self._monogram_paths))[:20],
            "keys": sorted({key[0] for key in self._cache}),
        }

    # -------------------------------------------------------------- wewnetrzne
    def _resolve(self, path: str, size: int) -> tuple[QPixmap, str]:
        """Zwraca (pixmap, zrodlo) probujac plik graficzny, potem powloke."""
        if not path:
            return QPixmap(), "monogram"
        try:
            info = QFileInfo(path)
            if not info.exists():
                return QPixmap(), "monogram"
            if f".{info.suffix().lower()}" in IMAGE_SUFFIXES:
                # ikona ze Sklepu: plik .png/.ico wskazany w manifescie AppX
                image = QPixmap(path)
                if not image.isNull():
                    if image.width() > size or image.height() > size:
                        image = image.scaled(
                            size,
                            size,
                            Qt.AspectRatioMode.KeepAspectRatio,
                            Qt.TransformationMode.SmoothTransformation,
                        )
                    return image, "image"
                # brak odczytu pliku graficznego (np. ACL WindowsApps) - powloka
            icon = self._icon_provider().icon(info)
            if not icon.isNull():
                pixmap = icon.pixmap(QSize(size, size))
                if not pixmap.isNull():
                    return pixmap, "shell"
        except Exception:  # pragma: no cover - zalezy od platformy
            return QPixmap(), "monogram"
        return QPixmap(), "monogram"

    def _extract(self, path: str, size: int) -> QPixmap:
        """Zgodnosc wstecz: sama ekstrakcja bez monogramu (do diagnostyki)."""
        pixmap, _source = self._resolve(path, size)
        return pixmap

    def _count(self, source: str, path: str) -> None:
        self._counters[source] = self._counters.get(source, 0) + 1
        if source == "monogram" and path:
            self._monogram_paths.append(path)

    def _icon_provider(self) -> QFileIconProvider:
        if self._provider is None:
            self._provider = QFileIconProvider()
        return self._provider


ICONS = IconCache()


def app_pixmap(icon_path: str, size: int = 40, gray: bool = True) -> QPixmap:
    """Skrot do wspolnego cache'u ikon."""
    return ICONS.pixmap(icon_path, size, gray)


def app_icon(entry: dict, size: int = 40, gray: bool = True) -> QPixmap:
    """Ikona dla wpisu katalogu aplikacji (`icon_path`, zapasowo `path`)."""
    data = entry if isinstance(entry, dict) else {}
    source = str(data.get("icon_path") or data.get("path") or "")
    label = str(data.get("name") or data.get("exe") or "")
    pixmap = ICONS.pixmap(source, size, gray)
    if pixmap.isNull():
        return ICONS.monogram(label, size)
    return pixmap
