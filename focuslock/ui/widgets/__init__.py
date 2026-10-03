"""Publiczne API warstwy widgetow (monochromatyczne, QPainter)."""
from __future__ import annotations

from .buttons import ChoiceGroup, DangerButton, GhostButton, PrimaryButton, Toggle, link_button
from .catalog import AppTile, ChipRow, PickList, SiteRow
from .graphs import Heatmap, MonochromeChart, dot_matrix
from .indicators import DitheredBar, RingProgress, Sparkline
from .labels import (
    ElidedLabel,
    body,
    caption,
    elided,
    empty,
    empty_detail,
    field,
    hint,
    subtitle,
    timer_label,
    title,
    value_label,
)
from .nav import NavRail
from .paint import annulus_path, dither_brush, fill_dither, gray_level, pen, qcolor
from .primitives import Card, EmptyState, Hairline, Separator, hr
from .tiles import Badge, StatTile, Toast

__all__ = [
    # kontrakt (sekcja 17 INTERFACES.md)
    "RingProgress",
    "DitheredBar",
    "Hairline",
    "Card",
    "GhostButton",
    "PrimaryButton",
    "StatTile",
    "MonochromeChart",
    "Heatmap",
    "Toast",
    "NavRail",
    "Toggle",
    # dodatki pomocnicze
    "AppTile",
    "Badge",
    "ChipRow",
    "ChoiceGroup",
    "DangerButton",
    "ElidedLabel",
    "EmptyState",
    "PickList",
    "Separator",
    "SiteRow",
    "Sparkline",
    "body",
    "caption",
    "dot_matrix",
    "elided",
    "empty",
    "empty_detail",
    "field",
    "hint",
    "hr",
    "link_button",
    "subtitle",
    "timer_label",
    "title",
    "value_label",
    "annulus_path",
    "dither_brush",
    "fill_dither",
    "gray_level",
    "pen",
    "qcolor",
]
