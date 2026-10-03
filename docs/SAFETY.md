# Cisza — bezpieczeństwo i wyjścia awaryjne

Dokument obowiązkowy: **przeczytaj go w całości, zanim włączysz tryb `hard` albo `hardcore`.**
Cisza potrafi zostawić pasek zadań tylko z dozwolonymi aplikacjami (albo ukryć go całkiem),
zmienić tapetę, przejąć skróty klawiaturowe, ubić lub wstrzymać aplikacje i przekierować ruch
sieciowy. Wszystko to jest odwracalne, ale musisz wiedzieć, którędy się wychodzi.

---

## 1. Zanim zaczniesz (3 minuty)

1. **Zapisz pracę** we wszystkich otwartych programach — tryb `hard`/`hardcore` potrafi je
   wstrzymać (`suspend`) albo zamknąć (`kill`).
2. Zapisz gdzieś **kombinację wyjścia** i **PIN** (PIN ustawiasz w onboardingu). Bez nich
   zostaje tylko awaryjne przytrzymanie klawiszy albo Ctrl+Alt+Del.
3. Ustal `LockConfig.emergency_hold_key = True` (domyślnie włączone) — to twoja "smycz".
4. Przy pierwszym uruchomieniu przetestuj **na sucho**: `CISZA_DRY_RUN=1` (patrz sekcja 6).
5. Nie testuj lockdownu w trakcie aktualizacji Windows ani gdy masz niezapisane dane.

---

## 2. Jak wyjść z trybu `hard` / `hardcore`

Kolejność od najwygodniejszego do najbardziej awaryjnego:

### 2.1. Kombinacja wyjścia + PIN (normalna droga)

1. Wciśnij **sekretną kombinację wyjścia**: `Ctrl+Alt+Shift+X` (wariant zapasowy:
   `Ctrl+Alt+Shift+Q`). Gdy okno Ciszy ma fokus, działa skrót aplikacji; gdy fokus ma inna
   aplikacja — globalny wariant instaluje helper przez hooki klawiatury
   (`HotkeyBlocker(on_exit=...)`).
2. Cisza pokazuje okno `PinDialog`.
3. Wpisz PIN. Poprawne wpisanie zdejmuje nakładki i uruchamia pełne przywracanie systemu
   (`focuslock.recovery.restore_everything`).
4. Po wyjściu obowiązuje `exit_cooldown_seconds` (domyślnie 60 s) — przez ten czas nie da się
   rozpocząć nowej sesji od razu. To celowe, żeby nie "przeklikać" wyjścia.

W trybie `hardcore` **nie ma** zwykłego przycisku "Zakończ". Wyjście wyłącznie kombinacją + PIN,
awaryjnym przytrzymaniem (jeśli bezpiecznik PIN-u jest wyłączony) albo Ctrl+Alt+Del.

### 2.2. Awaryjne przytrzymanie klawiszy

Przytrzymaj **`Ctrl+Alt+Shift+Q` przez ok. 5 sekund** (`hold_seconds`).
Po tym czasie Cisza wysyła `exit_request {"how": "hold"}`. Co się dzieje dalej, zależy od
przełącznika **`lock.panic_requires_pin`** (Ustawienia → "WYJŚCIE AWARYJNE WYMAGA PIN-U"):

| `panic_requires_pin` | Zachowanie |
| --- | --- |
| `True` (domyślnie) | pojawia się `PinDialog` — sesję kończysz PIN-em |
| `False` | sesja kończy się **natychmiast**, bez PIN-u; do bazy trafia zdarzenie `PANIC_EXIT {"how": "hold", "pin": false}` |

Warunek wstępny: `LockConfig.emergency_hold_key = True` (domyślnie). Wyłączając go w
Ustawieniach, zostawiasz sobie tylko kombinację wyjścia, `Ctrl+Alt+Del` i `restore.py --panic`.

### 2.3. Ctrl+Alt+Del — jedyne pewne wyjście

**Ctrl+Alt+Del** to tzw. Secure Attention Sequence. Obsługuje ją `winlogon.exe` w sesji
systemowej, zanim zdarzenie dotrze do jakiegokolwiek hooka klawiatury w trybie użytkownika.
Dlatego:

* Cisza **nie blokuje** Ctrl+Alt+Del i nigdy nie będzie tego potrafić — biblioteka `keyboard`
  ani żaden hook user-mode nie widzi tej kombinacji;
* to jedyne wyjście, które działa, gdy GUI zawiesi się, nakładki zamarzną, a helper przestanie
  odpowiadać.

Po wciśnięciu Ctrl+Alt+Del:

1. Wybierz **Menedżer zadań** (jeśli Cisza zablokowała Menedżera zadań jako aplikację, i tak
   uruchomi się on z ekranu bezpieczeństwa — ten ekran nie podlega blokadzie procesów).
2. Zakończ procesy: `pythonw.exe` (GUI) oraz `pythonw.exe`/`Cisza-helper.exe` (helper).
3. Jeśli system nadal wygląda "zablokowany", wybierz **Wyloguj** albo **Uruchom ponownie** —
   po restarcie Cisza sama odtworzy stan powłoki (sekcja 4).

### 2.4. Restart komputera

Restart jest zawsze bezpieczny: Cisza zapisuje stan lockdownu na dysku, a przy następnym
starcie przywraca powłokę i sieć. Nie ma potrzeby "naprawiania" czegokolwiek ręcznie.

---

## 3. `python tools/restore.py --panic` — wyjście z konsoli

Użyj tego, gdy GUI nie odpowiada, nie znasz PIN-u, albo po restarcie zostały ślady blokady.

1. Otwórz terminal: **Win+X → Terminal**, `cmd`, PowerShell albo — gdy skróty są przejęte —
   **Ctrl+Alt+Del → Menedżer zadań → Plik → Uruchom nowe zadanie**.
2. Przejdź do katalogu projektu i uruchom:

   ```bat
   cd C:\Users\Nelek\Downloads\Productivity
   python tools\restore.py --panic
   ```

3. `--panic` nie pyta o PIN — wymusza pełne przywracanie i dodatkowo zamyka działające
   procesy Ciszy (te z `focuslock`/`pythonw` w linii poleceń). Podgląd bez zmieniania systemu:

   ```bat
   python tools\restore.py --panic --dry-run
   ```

   To samo bez `tools/restore.py` (np. gdy nie masz ścieżki pod ręką) uruchomisz z launchera:

   ```bat
   python run.pyw --restore            :: przywroc powloke i siec
   python run.pyw --restore --dry-run  :: tylko podglad
   ```

Co robi `--panic` (poprzez `focuslock.recovery.restore_everything`):

| Obszar | Operacja |
| --- | --- |
| Procesy Ciszy | zamyka `pythonw.exe` z `focuslock` w linii poleceń (z wyjątkiem własnego procesu) |
| Pasek zadań | `shell.taskbarfilter.restore()` (przywraca przyciski zabrane w trybie `filtered`), potem `shell.taskbar.show()` — `Shell_TrayWnd` / `Shell_SecondaryTrayWnd` |
| Tapeta i ikony | `shell.desktop.restore()` — poprzednia tapeta i widoczność ikon z `lockstate.json` |
| Skróty | `shell.hotkeys.uninstall()` — zdejmuje blokady Win/Alt+Tab/Alt+F4 itd. |
| Toasty/dźwięk/sen | `shell.dnd.mute_toasts(False)`, `mute_sound(False)`, `prevent_sleep(False)` |
| Zapora | `block.firewall.remove_blocks()` — usuwa reguły `CiszaBlock-*` |
| Sieć | `NetworkLock().disable()` — proxy systemowe z HKCU, PAC, hosts, DNS |
| Stan | `lockstate.clear()` + usunięcie flagi nieczystego zamknięcia |

Wynik to raport `{"ok", "applied", "warnings", "errors"}` wypisany na konsolę.
**Hosts i reguły zapory wymagają uprawnień administratora** — jeśli w raporcie zobaczysz
`PermissionError` / błąd `netsh`, uruchom terminal jako administrator i powtórz polecenie
(jest idempotentne, powtórne uruchomienie nie szkodzi).

---

## 4. Zanik prądu, crash, zawieszenie GUI

Cisza nie zostawia systemu w stanie "nie do odkręcenia", bo stan blokady jest zapisywany
w `%LOCALAPPDATA%\Cisza\`:

| Plik | Znaczenie |
| --- | --- |
| `lockstate.json` | Co dokładnie zmieniono (tapeta, pasek, skróty, proxy, hosts, reguły zapory) — z tego odtwarzany jest stan pierwotny |
| `heartbeat` | Znacznik życia GUI; dotykany przy każdym ticku |
| `unclean.json` | Flaga nieczystego zamknięcia (crash / zanik prądu / kill procesu) |
| `cisza.db` | Baza sesji, banku i statystyk |

Co się dzieje dalej:

1. **Watchdog** (`focuslock/shell/watchdog.py`, proces `pythonw -m focuslock --watchdog`)
   sprawdza świeżość heartbeatu co 2 s. Gdy heartbeat jest starszy niż 10 s
   (`is_heartbeat_fresh() == False`) albo `lockstate.active` wskazuje na brak procesu GUI,
   wywołuje `restore_everything(reason="watchdog")` i kończy pracę.
2. **Po restarcie komputera** przy `SystemConfig.restore_on_boot = True` (domyślnie) Cisza
   najpierw wykonuje `restore_everything`, a dopiero potem pokazuje okno. Flaga crashu jest
   usuwana, a niedokończone sesje (`status RUNNING/ARMED`) są zamykane jako `CRASHED`.
   Jeśli wyłączysz tę opcję, sesje nadal zostaną domknięte, ale powłoka i sieć **nie** zostaną
   ruszone — `lockstate.json` zostaje na dysku (z `active=false`), żeby `restore.py --panic`
   wiedziało, co cofnąć.
3. **Po zaniku prądu nic nie rób ręcznie**: uruchom komputer, poczekaj na start Ciszy,
   a jeśli pasek zadań/tapeta/proxy nadal wyglądają na zablokowane — `python tools\restore.py --panic`.
4. Jeśli chcesz sprawdzić stan bez uruchamiania GUI:

   ```bat
   type %LOCALAPPDATA%\Cisza\lockstate.json
   dir %LOCALAPPDATA%\Cisza
   ```

   `lockstate.json` mówi, co trzeba cofnąć. **Nie kasuj go ręcznie** — usuń najpierw blokadę
   przez `restore.py`, bo plik jest jedyną pamięcią poprzedniej tapety i konfiguracji proxy.

---

## 5. Tryb awaryjny i tryb "na sucho"

### 5.1. Tryb awaryjny (`--safe`)

```bat
pythonw run.pyw --safe
```

albo bez argumentu, zmienną środowiskową:

```bat
set CISZA_SAFE=1
pythonw run.pyw
```

Tryb awaryjny wchodzi bez blokad powłoki i bez restartu poprzedniego lockdownu — służy do
diagnostyki: sprawdzenia ustawień, eksportu statystyk i uruchomienia `restore.py`.

### 5.2. Symulacja bez dotykania systemu (`CISZA_DRY_RUN`)

```bat
set CISZA_DRY_RUN=1
python run.pyw
```

W trybie `dry_run` żadna operacja systemowa nie jest wykonywana — funkcje zwracają tylko raport
`{"ok", "applied", "warnings", "errors"}` z listą tego, co *by* zrobiły. To właściwy sposób
testowania hardcore bez ryzyka. Użyj konsoli (`python run.pyw`, nie `pythonw`), żeby widzieć logi.

### 5.3. Przekierowanie danych

```bat
set CISZA_DATA_DIR=%TEMP%\cisza-test
```

Wszystkie pliki (baza, lockstate, heartbeat, token helpera) trafiają wtedy do wskazanego
katalogu — nic nie dotyka prawdziwego `%LOCALAPPDATA%\Cisza`. Tej zmiennej używają też testy.

---

## 6. Uruchomienie po "twardym" wyjściu — kolejność działań

1. Ctrl+Alt+Del → Menedżer zadań → zakończ procesy Ciszy (GUI i helper).
2. `python tools\restore.py --panic` (najlepiej w terminalu jako administrator).
3. Sprawdź: pasek zadań widoczny, tapeta wróciła, Menedżer zadań i Win+R działają,
   przeglądarka nie jest zmuszona do proxy `127.0.0.1:8765`.
4. Jeśli proxy zostało: Ustawienia → Sieć i internet → Proxy → wyłącz ręcznie (Cisza i tak
   przywraca poprzednią konfigurację z `lockstate.json`).
5. Uruchom `pythonw run.pyw --safe` i przejrzyj Ustawienia/Statystyki.

---

## 7. Ostrzeżenia

* **Nie uruchamiaj `hard`/`hardcore` bez zapisanej pracy.** `action="kill"` zamyka procesy bez
  pytania o zapis; `suspend` potrafi zawiesić program z otwartym dokumentem.
* **Lista aplikacji z kreatora sesji decyduje, co przetrwa naukę.** Wszystko, czego nie zaznaczysz
  w kroku 1, może zostać wstrzymane albo ubite — zanim włączysz `hard`/`hardcore`, przejrzyj
  zaznaczenie (albo sprawdź plan w trybie `CISZA_DRY_RUN=1`). Cisza nigdy nie rusza procesów
  systemowych ani własnych.
* **Katalog aplikacji tylko czyta system** (Menu Start, Sklep, klucze `Uninstall`, otwarte
  procesy) i nie wymaga administratora. Cache `%LOCALAPPDATA%\Cisza\apps_cache.json` możesz
  bezpiecznie usunąć — zostanie odbudowany przy następnym skanie.
* **Hardcore z PIN-em, którego nie pamiętasz, to ślepa uliczka** poza wyłączonym bezpiecznikiem
  PIN-u ("wyjście awaryjne wymaga PIN-u" = wył.), `Ctrl+Alt+Del` i `restore.py --panic`.
  Zapisz PIN poza komputerem.
* **Blokada Menedżera zadań** działa tylko w zwykłej sesji — ekran Ctrl+Alt+Del zostaje
  nienaruszony i to on jest siatką bezpieczeństwa.
* **Pasek zadań w trybie `filtered` (domyślnym) zostaje widoczny**, ale Cisza zabiera z niego
  przyciski okien aplikacji spoza listy dozwolonych (`ITaskbarList::DeleteTab`); wracają po
  zakończeniu sesji, a stan filtra leży w `%LOCALAPPDATA%\Cisza\backup\taskbar_filter.json`.
  Pełne ukrycie paska to `taskbar_mode = "hide"` (Ustawienia → Blokada); wtedy brak Alt+Tab
  i ukryty pasek utrudniają korzystanie z komputera — to zamierzone, ale oznacza, że wyjście
  trzeba znać *przed* startem.
* **Blokada `hosts` i reguły zapory wymagają administratora** (UAC). Bez zgody UAC zadanie
  nie zostanie wykonane — Cisza zapisze to jako ostrzeżenie, a nie cichy błąd.
* **Nie zmieniaj ręcznie `%LOCALAPPDATA%\Cisza\lockstate.json`** i nie kasuj go, dopóki
  cokolwiek jest zablokowane.
* **Tryb `dry_run` nie uruchamia blokad** — jeśli coś *nadal* jest zablokowane przy
  `CISZA_DRY_RUN=1`, to znaczy, że blokadę zostawił wcześniejszy bieg; użyj `restore.py`.
* **Watchdog to nie magiczna różdżka**: jeśli zostanie ubity razem z GUI (np. przy twardym
  resecie), blokadę cofnie `restore_on_boot` przy następnym starcie albo ręczny `restore.py`.

---

## 8. Karta awaryjna (skrót)

```text
WYJŚCIE NORMALNE : Ctrl+Alt+Shift+X (albo Q)  ->  PIN
WYJŚCIE AWARYJNE : przytrzymaj Ctrl+Alt+Shift+Q ~5 s
                   (panic_requires_pin=True -> PIN; False -> koniec od razu)
WYJŚCIE PEWNE    : Ctrl+Alt+Del  ->  Menedżer zadań  ->  zakończ pythonw.exe
Z KONSOLI        : python tools\restore.py --panic
PODGLĄD          : python tools\restore.py --panic --dry-run
TRYB AWARYJNY    : pythonw run.pyw --safe     (albo CISZA_SAFE=1)
NA SUCHO         : set CISZA_DRY_RUN=1 && python run.pyw
DANE             : %LOCALAPPDATA%\Cisza\  (lockstate.json, heartbeat, unclean.json, cisza.db)
```

Powiązane dokumenty: [README.md](README.md), [INTERFACES.md](INTERFACES.md).
