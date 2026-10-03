# Cisza — projekt techniczny

## 1. Cel

Aplikacja dla Windows 11, która po rozpoczęciu sesji zamienia komputer w monochromatyczną
maszynę do nauki: brak tapety, brak paska zadań, brak ikon pulpitu, brak aplikacji i stron
spoza listy dozwolonych. Czas nauki przelicza się na czas wolny (bank), wszystko jest mierzone
i widoczne w statystykach.

## 2. Procesy

| Proces | Uprawnienia | Odpowiedzialność |
|---|---|---|
| `pythonw -m focuslock` (GUI) | użytkownik | UI, licznik, ekonomia, baza, tray, nakładki |
| `pythonw -m focuslock --helper` | administrator (UAC) | powłoka, hosts, zapora, proxy, guard procesów, hooki |
| `pythonw -m focuslock --watchdog` | użytkownik | heartbeat GUI → przywrócenie powłoki po crashu |
| `python tools/restore.py --panic` | użytkownik | ręczne, awaryjne przywrócenie powłoki |

GUI komunikuje się z helperem przez nazwany potok (`\\.\pipe\cisza-helper-<user>`) z podwójnym
zabezpieczeniem: `authkey` (HMAC handshake `multiprocessing.connection`) + token w każdej
wiadomości. Kontrakt RPC: `focuslock/ipc.py`, `focuslock/helper.py`, `focuslock/helperclient.py`.

## 3. Przepływ sesji nauki

1. Ekran startowy (`ui/screens/home.py`) → kreator (`composer.py`) → `Controller.start_study(payload)`.
2. `Store.start_session(...)` tworzy rekord, `Economy`/`SessionEngine` startują licznik.
3. `Controller._lockdown()`:
   - `helper.shell_lock` — czarna tapeta, ukrycie ikon i paska zadań, hooki klawiatury, DND, blokada snu,
   - `helper.guard_start` — lista dozwolonych aplikacji (reszta suspendowana/ubijana),
   - `helper.network_enable` — proxy allowlist + hosts/blocklist + zapora,
   - `OverlayManager.lock()` — czarne nakładki na pozostałych ekranach.
4. `QTimer` co sekundę: `Controller.tick()` → `SessionEngine.tick()`, heartbeat dla watchdoga,
   reasekuracja nakładek, zbieranie zdarzeń blokad do bazy.
5. Koniec pomodoro → `Economy.credit_pomodoro()` (ulga 0.5, limit dzienny, kara za przerwanie).
6. Koniec sesji → `Store.finish_session`, `Economy.register_study_day` (seria), `Controller._release()`
   → zdjęcie wszystkich blokad, wyczyszczenie `lockstate`.

Wszystko, co zmieniamy w systemie, jest zapisywane w `%LOCALAPPDATA%\Cisza\lockstate.json`
(stan tapety, ikon, paska, hooków, proxy, hosts, zapory). Ten plik jest podstawą odtwarzania.

## 4. Ekonomia

| Parametr | Wartość domyślna |
|---|---|
| Ulga (`earn_ratio`) | 0.5 (25 min nauki → 12 min 30 s wolnego) |
| Zaokrąglanie | 15 s w dół |
| Limit dzienny | 60 min zarobku |
| Wygasanie banku | 7 dni (FIFO, najstarsze minuty pierwsze) |
| Kara za przerwanie pomodoro | brak zarobku + zerwanie serii (+ opcjonalna kara minutowa) |
| Minimalny blok wolnego | 10 min |
| Seria | dzień z nauką ≥ 25 min |

Bank trzymany jest w dwóch tabelach: `bank_lots` (stan minut z datą wygaśnięcia) i `bank_ledger`
(pełna historia operacji: EARNED / SPENT / EXPIRED / PENALTY / ADJUST).

## 5. Blokady

* **Aplikacje** — `ProcessGuard` (psutil, 0.75 s) + `LaunchWatcher` (WMI `Win32_ProcessStartTrace`,
  fallback: odpytywanie). Procesy systemowe z listy `SYSTEM_SAFE` nigdy nie są ruszane.
* **Strony (nauka)** — lokalne proxy `127.0.0.1:8765` w trybie allowlist (HTTP + `CONNECT` bez MITM),
  ustawione jako proxy systemowe; reguły zapory blokują bezpośredni ruch 80/443 przeglądarkom.
* **Strony (przerwa/wolne)** — lista rozpraszaczy w `hosts` między znacznikami `# CISZA-BEGIN/END`.
* **Powłoka** — tapeta, ikony, pasek zadań (`Shell_TrayWnd`, `Shell_SecondaryTrayWnd`), hooki
  klawiatury (Win, Alt+Tab, Alt+F4, Ctrl+Esc, Ctrl+Shift+Esc), DND, blokada snu.
* **Hardcore** — dodatkowo `DisableTaskMgr`, watchdog restartujący GUI, brak wyjścia w trakcie sesji,
  bezpiecznik: maksymalny czas sesji hardcore (domyślnie 180 min) z automatycznym odblokowaniem.

## 6. Wygląd

Wyłącznie skala szarości (tokeny w `focuslock/ui/theme.py`), typografia Segoe UI Variable Display
+ Cascadia Mono, stany wyrażane glifem, mrugnięciem, ditheringiem i grubością linii.
Reguła jest wymuszana testem `tests/test_theme_mono.py` i narzędziem `tools/mono_lint.py`.

## 7. Tryby bezpieczeństwa

* `--dry-run` (`CISZA_DRY_RUN=1`) — nic nie zmienia w systemie, tylko loguje i raportuje.
* `--safe` (`CISZA_SAFE=1`) — pomija lockdown powłoki i sieci (zostaje licznik i statystyki).
* `python tools/restore.py --panic` — przywraca powłokę i zamyka procesy Ciszy.
* `Ctrl+Alt+Del` jest nieblokowalny i pozostaje ostatnią deską ratunku.

## 8. Testy

`python -m pytest tests/ -q` — jednostkowe (ekonomia, FSM, allowlisty, hosts, proxy, monochromatyczność)
oraz integracyjne (`tests/integration/`: pełny obieg w trybie dry-run, odtwarzanie po crashu).
