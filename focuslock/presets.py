"""Presety sesji: domyslne, zapis/odczyt w Store, normalizacja do ``Plan``.

Nie ma zamrozonego kontraktu w docs/INTERFACES.md - modul jest uzupelnieniem
sekcji 15-16. Mutacje zwracaja raport zgodny z ogolna konwencja projektu:
``{"ok", "applied", "warnings", "errors"}`` plus pola dodatkowe.

Mapowanie preset -> Plan:
- ``study_minutes`` -> ``study_seconds`` (x60), ``break_minutes`` -> ``break_seconds``,
  ``long_break_minutes`` -> ``long_break_seconds``, ``free_minutes`` -> ``free_seconds``;
- ``study_apps``, ``study_sites``, ``break_apps``, ``break_sites``, ``block_apps``,
  ``block_sites``, ``safe_hosts`` sa przenoszone do ``Plan.allowlist`` (bez zmian nazw),
- ``mode`` jest normalizowane do ``STUDY``/``FREE``.

Zero zaleznosci od PyQt i Windows.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from .config import default_presets
from .session import Plan

__all__ = [
    "ensure_defaults",
    "list_presets",
    "save_preset",
    "delete_preset",
    "apply_preset",
    "normalize_payload",
    "PLAN_KEYS",
]

#: Klucze, ktore ``normalize_payload`` przenosi do ``Plan.allowlist``.
ALLOWLIST_KEYS = (
    "study_apps",
    "study_sites",
    "break_apps",
    "break_sites",
    "block_apps",
    "block_sites",
    "safe_hosts",
)

#: Klucze planu zwracane przez ``apply_preset`` (bez metadanych raportu).
PLAN_KEYS = (
    "mode",
    "study_seconds",
    "break_seconds",
    "long_break_seconds",
    "long_break_every",
    "arm_seconds",
    "free_seconds",
    "tag",
    "goal_note",
    "allowlist",
)


def _report(ok: bool = True, applied: Optional[list] = None, errors: Optional[list] = None,
            warnings: Optional[list] = None, **extra: Any) -> dict:
    payload = {
        "ok": bool(ok),
        "applied": list(applied or []),
        "warnings": list(warnings or []),
        "errors": list(errors or []),
    }
    payload.update(extra)
    return payload


def _as_payload(value: Any) -> dict:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def normalize_payload(payload: Any) -> dict:
    """Normalizuje payload presetu do slownika pol ``Plan`` (gotowy dla silnika sesji)."""
    data = _as_payload(payload)
    allowlist = data.get("allowlist")
    allowlist = dict(allowlist) if isinstance(allowlist, dict) else {}
    for key in ALLOWLIST_KEYS:
        value = data.get(key)
        if isinstance(value, (list, tuple)):
            allowlist[key] = [str(item) for item in value]
    merged = dict(data)
    merged["allowlist"] = allowlist
    return Plan.from_dict(merged).to_dict()


def ensure_defaults(store) -> dict:
    """Wstawia ``config.default_presets()``, jesli brakuje ich w bazie (idempotentne)."""
    try:
        existing = {str(row.get("name")) for row in store.list_presets()}
    except Exception as exc:  # noqa: BLE001
        return _report(False, errors=[f"{type(exc).__name__}: {exc}"])
    added: list[str] = []
    errors: list[str] = []
    for name, payload in default_presets().items():
        if name in existing:
            continue
        try:
            store.upsert_preset(name, payload)
            added.append(name)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{name}: {type(exc).__name__}: {exc}")
    return _report(not errors, applied=added, errors=errors, count=len(added))


def list_presets(store) -> list[dict]:
    """Lista presetow z bazy (kazdy: id, name, payload, created_at)."""
    try:
        return list(store.list_presets())
    except Exception:  # noqa: BLE001
        return []


def save_preset(store, name: str, payload: dict) -> dict:
    """Zapisuje preset pod nazwa (nadpisuje istniejacy). Zwraca raport z ``id``."""
    clean = str(name or "").strip()
    if not clean:
        return _report(False, errors=["pusta nazwa presetu"], id=0)
    data = _as_payload(payload)
    if not data:
        return _report(False, errors=["payload musi byc niepustym dict-em"], id=0)
    try:
        preset_id = int(store.upsert_preset(clean, data))
    except Exception as exc:  # noqa: BLE001
        return _report(False, errors=[f"{type(exc).__name__}: {exc}"], id=0)
    return _report(True, applied=[clean], id=preset_id, name=clean)


def delete_preset(store, id) -> dict:
    """Usuwa preset po id. Zwraca raport (``applied`` zawiera opis)."""
    try:
        preset_id = int(id)
    except (TypeError, ValueError):
        return _report(False, errors=["id musi byc liczba"])
    try:
        store.delete_preset(preset_id)
    except Exception as exc:  # noqa: BLE001
        return _report(False, errors=[f"{type(exc).__name__}: {exc}"])
    return _report(True, applied=[f"preset:{preset_id}"], id=preset_id)


def apply_preset(store, name: str) -> dict:
    """Zwraca znormalizowany plan presetu.

    Wynik zawiera pola ``Plan`` na najwyzszym poziomie (mozna je podac wprost do
    ``Plan.from_dict`` / ``SessionEngine.start``) oraz metadane: ``ok``, ``name``,
    ``preset_id``, ``warnings``, ``errors``. Brak presetu -> ``ok=False``.
    """
    wanted = str(name or "").strip()
    if not wanted:
        return _report(False, errors=["pusta nazwa presetu"], name="")
    rows = list_presets(store)
    row = next((r for r in rows if str(r.get("name")) == wanted), None)
    if row is None:
        row = next((r for r in rows if str(r.get("name", "")).lower() == wanted.lower()), None)
    if row is None:
        return _report(False, errors=[f"brak presetu: {wanted}"], name=wanted, plan=None)
    plan = normalize_payload(row.get("payload"))
    report = _report(True, applied=[str(row.get("name"))])
    report.update(
        {
            "name": str(row.get("name")),
            "preset_id": int(row.get("id") or 0),
            "plan": plan,
            "source_payload": _as_payload(row.get("payload")),
        }
    )
    report.update({key: plan[key] for key in PLAN_KEYS})
    return report
