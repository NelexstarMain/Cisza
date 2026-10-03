"""Głęboki audyt: sprawdza, że KAŻDY przycisk, ustawienie, sygnał i akcja
ma kompletną ścieżkę od kliknięcia w UI do obsługi w kontrolerze.

Powstał jako odpowiedź na pytanie „czy wszystko działa jak powinno" —
test statyczny (bez QApplication), ale wnikliwszy niż test_ui_buttons_audit.

Sprawdza:
1. Każde pole z FIELD_SPECS w settings.py ma odpowiadające pole w dataclass config.py.
2. Każda akcja emitowana z ekranów ma odbiorcę w handle_action lub _run_action.
3. Nowe funkcjonalności: dźwięk, tray show_requested, admin indicator, custom categories.
4. Import as_int w settings.py (regresja naprawionego buga).
5. Sygnały tray są połączone w app.py.
6. Ekran summary odbiera zdarzenie session_finished.
7. Wszystkie ekrany z on_action_result obsługują właściwe akcje.
"""
from __future__ import annotations

import inspect
import os
import re
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
UI_DIR = ROOT / "focuslock" / "ui"
SCREENS_DIR = UI_DIR / "screens"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


# ---------------------------------------------------------------- 1. FIELD_SPECS → config
class TestSettingsFieldsMatchConfig:
    """Każde pole formularza w settings.py musi mieć odpowiednik w config.py."""

    def test_import_as_int_in_settings(self):
        """settings.py musi importować as_int z .base (używa go w diagnostyce)."""
        source = _read(SCREENS_DIR / "settings.py")
        # sprawdzamy import
        assert re.search(r"from\s+\.base\s+import\s+.*\bas_int\b", source), (
            "settings.py używa as_int ale go nie importuje z .base"
        )

    def test_all_field_specs_exist_in_config(self):
        """Każde pole z FIELD_SPECS musi istnieć w odpowiednim dataclass z config.py."""
        from focuslock.config import (
            EconomyConfig,
            LockConfig,
            NetworkConfig,
            SessionConfig,
            SystemConfig,
            UiConfig,
        )
        from focuslock.ui.screens.settings import FIELD_SPECS

        config_map = {
            "session": SessionConfig,
            "economy": EconomyConfig,
            "lock": LockConfig,
            "network": NetworkConfig,
            "ui": UiConfig,
            "system": SystemConfig,
        }
        missing = []
        for group, specs in FIELD_SPECS.items():
            cls = config_map.get(group)
            if cls is None:
                missing.append(f"grupa {group!r} nie ma odpowiednika w config.py")
                continue
            fields = {f.name for f in cls.__dataclass_fields__.values()}
            for spec in specs:
                field_name = spec[0]
                if field_name not in fields:
                    missing.append(f"{group}.{field_name} nie istnieje w {cls.__name__}")
        assert missing == [], f"pola z FIELD_SPECS bez odpowiednika w config: {missing}"

    def test_apply_settings_roundtrip(self):
        """Settings.to_dict → from_dict nie gubi żadnego pola z FIELD_SPECS."""
        from focuslock.config import Settings

        original = Settings()
        data = original.to_dict()
        restored = Settings.from_dict(data)

        from focuslock.ui.screens.settings import FIELD_SPECS

        for group, specs in FIELD_SPECS.items():
            orig_group = getattr(original, group, None)
            rest_group = getattr(restored, group, None)
            if orig_group is None or rest_group is None:
                continue
            for spec in specs:
                name = spec[0]
                orig_val = getattr(orig_group, name, "MISSING")
                rest_val = getattr(rest_group, name, "MISSING")
                assert orig_val == rest_val, (
                    f"roundtrip zgubił {group}.{name}: {orig_val!r} → {rest_val!r}"
                )


# ---------------------------------------------------------------- 2. Akcje UI → kontroler
class TestAllActionsHaveReceivers:
    """Każda akcja emitowana z UI musi mieć odbiorcę."""

    ACTION_RE = re.compile(r"""request_action\.emit\(\s*["']([a-z_]+)["']""")

    def _all_ui_actions(self) -> dict[str, list[str]]:
        found: dict[str, list[str]] = {}
        for path in sorted(UI_DIR.rglob("*.py")):
            text = _read(path)
            for match in self.ACTION_RE.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                found.setdefault(match.group(1), []).append(
                    f"{path.relative_to(ROOT).as_posix()}:{line}"
                )
        return found

    def test_every_action_handled(self):
        """Każda akcja z UI ma gałąź w handle_action lub _run_action."""
        app_module = pytest.importorskip("focuslock.app")
        from focuslock.controller import Controller

        ui_actions = self._all_ui_actions()
        action_source = inspect.getsource(app_module.MainWindow._run_action)
        controller_source = inspect.getsource(Controller.handle_action)
        known = (
            set(app_module.NAVIGATION_ACTIONS)
            | {"open_pin_dialog"}
            | set(re.findall(r'"([a-z_]+)"', action_source))
            | set(re.findall(r'"([a-z_]+)"', controller_source))
        )
        missing = {a: locs for a, locs in ui_actions.items() if a not in known}
        assert missing == {}, f"akcje bez odbiorcy: {missing}"


# ---------------------------------------------------------------- 3. Dźwięk (nowa funkcja)
class TestSoundIntegration:
    """Moduł dźwięku i jego integracja z app.py."""

    def test_sound_module_exists(self):
        sound_path = UI_DIR / "sound.py"
        assert sound_path.exists(), "brak focuslock/ui/sound.py"

    def test_sound_play_function(self):
        from focuslock.ui import sound
        assert hasattr(sound, "play_sound"), "sound.py nie eksportuje play_sound()"

    def test_sound_events_in_app(self):
        """app.py wywołuje play_sound na zdarzenia break_start, study_start, pomodoro_end."""
        app_source = _read(ROOT / "focuslock" / "app.py")
        for event in ("break_start", "study_start", "pomodoro_end"):
            assert f'play_sound("{event}")' in app_source, (
                f"app.py nie wywołuje play_sound({event!r})"
            )

    def test_sound_respects_mute_settings(self):
        """Dźwięki są warunkowane przez sound_enabled i mute_sound."""
        app_source = _read(ROOT / "focuslock" / "app.py")
        assert "sound_enabled" in app_source, "app.py nie sprawdza sound_enabled"
        assert "mute_sound" in app_source, "app.py nie sprawdza mute_sound"


# ---------------------------------------------------------------- 4. Tray (nowa funkcja)
class TestTrayShowRequested:
    """Ikona w zasobniku: show_requested przywraca okno."""

    def test_show_requested_signal_exists(self):
        from focuslock.ui.tray import Tray
        assert hasattr(Tray, "show_requested"), "Tray nie ma sygnału show_requested"

    def test_show_requested_connected_in_app(self):
        app_source = _read(ROOT / "focuslock" / "app.py")
        assert "tray.show_requested.connect(" in app_source, (
            "show_requested nie jest podpięty w app.py"
        )

    def test_restore_from_tray_method_exists(self):
        app_module = pytest.importorskip("focuslock.app")
        assert hasattr(app_module.MainWindow, "restore_from_tray"), (
            "MainWindow nie ma metody restore_from_tray"
        )

    def test_restore_from_tray_routes_to_screens(self):
        """restore_from_tray musi kierować na running/break/free/home."""
        source = inspect.getsource(
            pytest.importorskip("focuslock.app").MainWindow.restore_from_tray
        )
        for screen in ("running", "break", "free", "home"):
            assert f'"{screen}"' in source, (
                f"restore_from_tray nie kieruje na ekran {screen!r}"
            )

    def test_pokaz_cisze_action_in_tray_menu(self):
        """Menu kontekstowe tray ma akcję 'Pokaż Ciszę'."""
        tray_source = _read(UI_DIR / "tray.py")
        assert "Pokaż Ciszę" in tray_source, "brak akcji 'Pokaż Ciszę' w tray.py"


# ---------------------------------------------------------------- 5. Admin indicator (nowa funkcja)
class TestAdminIndicator:
    """Wskaźnik KONTROLA: PEŁNA/PODSTAWOWA na ekranach."""

    def test_is_elevated_in_controller(self):
        from focuslock.controller import Controller
        assert hasattr(Controller, "is_elevated"), "Controller nie ma metody is_elevated"

    def test_summary_includes_admin_fields(self):
        """summary() musi zwracać is_admin i control_level."""
        source = inspect.getsource(
            pytest.importorskip("focuslock.controller").Controller.summary
        )
        assert "is_admin" in source, "summary() nie zawiera is_admin"
        assert "control_level" in source, "summary() nie zawiera control_level"

    @pytest.mark.parametrize("screen_file", ["home.py", "running.py", "settings.py"])
    def test_control_label_on_screens(self, screen_file):
        """Ekran wyświetla etykietę KONTROLA."""
        source = _read(SCREENS_DIR / screen_file)
        assert "KONTROLA" in source, f"{screen_file} nie wyświetla wskaźnika KONTROLA"

    def test_helper_admin_in_bridge(self):
        source = _read(ROOT / "focuslock" / "helperclient.py")
        assert "helper_admin" in source, "helperclient.py nie śledzi helper_admin"


# ---------------------------------------------------------------- 6. Własne kategorie stron (nowa funkcja)
class TestCustomSiteCategories:
    """Katalog stron z własnymi kategoriami."""

    def test_custom_categories_in_sitecatalog(self):
        from focuslock import sitecatalog
        assert hasattr(sitecatalog, "catalog_payload_with_custom"), (
            "sitecatalog nie ma catalog_payload_with_custom"
        )

    def test_wlasne_category_in_settings_ui(self):
        """Combo kategoria stron w settings.py zawiera WŁASNA."""
        source = _read(SCREENS_DIR / "settings.py")
        assert "wlasne" in source, "settings.py nie zawiera kategorii 'wlasne'"
        assert "wlasne_blok" in source, "settings.py nie zawiera kategorii 'wlasne_blok'"

    def test_editable_combo(self):
        """Combo kategorii stron jest edytowalne."""
        source = _read(SCREENS_DIR / "settings.py")
        assert "setEditable(True)" in source, (
            "combo kategorii stron nie jest edytowalne (brak setEditable(True))"
        )

    def test_save_site_preserves_custom_category(self):
        """save_site_profile w kontrolerze zachowuje niestandardowe kategorie."""
        source = inspect.getsource(
            pytest.importorskip("focuslock.controller").Controller.save_site_profile
        )
        # Musi mieć gałąź else, która zachowuje raw_cat
        assert "raw_cat" in source, "save_site_profile nie zachowuje oryginalnej kategorii"

    def test_controller_merges_custom_profiles_into_catalog(self):
        """catalog_sites w handle_action scala custom profiles."""
        source = inspect.getsource(
            pytest.importorskip("focuslock.controller").Controller.handle_action
        )
        assert "catalog_payload_with_custom" in source, (
            "handle_action(catalog_sites) nie wywołuje catalog_payload_with_custom"
        )


# ---------------------------------------------------------------- 7. Kompletność on_action_result
class TestOnActionResultReceivers:
    """Ekrany z on_action_result muszą obsługiwać właściwe akcje."""

    EXPECTED = {
        "refresh_bank": "bank.py",
        "refresh_stats": "stats.py",
        "stats_period": "stats.py",
        "refresh_journal": "journal.py",
        "diagnostics": "settings.py",
    }

    @pytest.mark.parametrize("action,screen_file", list(EXPECTED.items()))
    def test_action_result_received(self, action, screen_file):
        source = _read(SCREENS_DIR / screen_file)
        assert "def on_action_result" in source, (
            f"{screen_file} nie ma on_action_result"
        )
        assert action in source, (
            f"{screen_file}.on_action_result nie obsługuje akcji {action!r}"
        )


# ---------------------------------------------------------------- 8. Ekran summary odbiera session_finished
class TestSummarySessionFinished:
    def test_summary_has_on_app_event(self):
        source = _read(SCREENS_DIR / "summary.py")
        assert "def on_app_event" in source, (
            "SummaryScreen nie ma on_app_event → wynik sesji nigdy nie dociera"
        )
        assert "session_finished" in source, (
            "SummaryScreen.on_app_event nie reaguje na session_finished"
        )


# ---------------------------------------------------------------- 9. Nawigacja + PIN
class TestNavigationAndPin:
    def test_navigation_actions_complete(self):
        """NAVIGATION_ACTIONS pokrywa wszystkie ekrany z SCREEN_MODULES."""
        app_module = pytest.importorskip("focuslock.app")
        for screen_name in app_module.SCREEN_MODULES:
            action = f"open_{screen_name}"
            assert action in app_module.NAVIGATION_ACTIONS or action in inspect.getsource(
                app_module.MainWindow._run_action
            ), f"brak nawigacji do ekranu {screen_name!r}"

    def test_pin_dialog_connected(self):
        """open_pin_dialog ma ścieżkę w _run_action."""
        source = inspect.getsource(
            pytest.importorskip("focuslock.app").MainWindow._run_action
        )
        assert "open_pin_dialog" in source

    def test_pin_check_and_set_in_controller(self):
        from focuslock.controller import Controller
        assert hasattr(Controller, "check_pin")
        assert hasattr(Controller, "set_pin")


# ---------------------------------------------------------------- 10. Shutdown z tray
class TestTrayShutdown:
    def test_quit_goes_through_shutdown(self):
        """'Zamknij' z tray musi wołać shutdown(), a nie app.quit() bezpośrednio."""
        app_source = _read(ROOT / "focuslock" / "app.py")
        match = re.search(r"tray\.quit_requested\.connect\(([^)]*)\)", app_source)
        assert match is not None, "quit_requested nie jest podpięty"
        target = match.group(1).strip()
        assert "shutdown" in target or "close" in target, (
            f"quit_requested → {target}: pomija shutdown (blokady mogą zostać)"
        )
