# Cisza

Monochromatyczna aplikacja dla Windows 11, która zamienia naukę w walutę: za ukończone
pomodoro dostajesz minuty "wolnego", a w trakcie sesji Cisza zamyka rozpraszacze —
aplikacje, strony i powiadomienia. Wszystko czarno-białe, bez animowanego lukru.

* **Wymusza naukę**: tryby blokad `soft` / `hard` / `hardcore`, blokada procesów, sieci
  (proxy + `hosts` + zapora), paska zadań (widoczny, ale tylko z dozwolonymi aplikacjami),
  tapety i skrótów klawiaturowych.
* **Nie zamyka cię na zawsze**: każde wyjście awaryjne jest opisane w [SAFETY.md](SAFETY.md) —
  przeczytaj ten dokument przed pierwszym hardcore.
* **Płaci za naukę**: bank minut z limitem dziennym, TTL i serią dni.

---

## 1. Wymagania

| Element | Wersja / uwagi |
| --- | --- |
| System | Windows 11 (Windows 10 zadziała, ale nie jest testowany) |
| Python | 3.13 (3.11+ powinno działać) |
| Uprawnienia | **Brak wymaganych.** Administrator/UAC potrzebny tylko dla blokady `hosts` i reguł zapory |
| Zależności | PyQt6, psutil, keyboard, Pillow, matplotlib, pywin32-ctypes |

## 2. Instalacja

```bat
cd C:\Users\Nelek\Downloads\Productivity
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Opcjonalnie, żeby uruchamiać projekt bez `cd`:

```bat
setx PYTHONPATH C:\Users\Nelek\Downloads\Productivity
```

Nie musisz nic instalować "jako administrator" i nie musisz niczego wgrywać do rejestru —
Cisza trzyma wszystkie dane w `%LOCALAPPDATA%\Cisza`.

## 3. Uruchomienie

```bat
pythonw run.pyw
```

`run.pyw` uruchamia GUI bez okna konsoli. Do podglądu logów użyj:

```bat
python run.pyw
```

Pierwsze uruchomienie przechodzi przez onboarding: ustawiasz PIN, wybierasz tryb blokady
i decydujesz o autostarcie. Kolejne uruchomienia startują zminimalizowane do zasobnika.

Przydatne warianty:

```bat
python run.pyw --safe          :: tryb awaryjny: bez blokad powloki
python run.pyw --dry-run       :: symulacja, zero zmian w systemie
python run.pyw --restore       :: awaryjne przywrocenie powloki i sieci
python run.pyw --watchdog      :: pilnowanie zycia GUI (proces w tle)
python run.pyw --version       :: wersja
set CISZA_DRY_RUN=1 && python run.pyw   :: to samo co --dry-run, przez srodowisko
set CISZA_DATA_DIR=%TEMP%\cisza         :: osobny katalog danych (testy)
```

## 4. Sterowanie

* **Zasobnik systemowy (tray)**: Start sesji, Zakończ, Statystyki, Wyjdź.
* **Sekretna kombinacja wyjścia `Ctrl+Alt+Shift+X`** (zapasowo `Ctrl+Alt+Shift+Q`) → okno PIN-u;
  działa też globalnie, gdy fokus ma inna aplikacja.
* **Awaryjne przytrzymanie `Ctrl+Alt+Shift+Q`** przez ok. 5 s: domyślnie kończy się oknem PIN-u,
  a przy wyłączonym „wyjście awaryjne wymaga PIN-u" — natychmiastowym końcem sesji.
* **`Ctrl+Alt+Del`**: zawsze działa i zawsze kończy blokadę — szczegóły w [SAFETY.md](SAFETY.md).
* **Ekrany**: Start (timer, szybki start z presetów), Kompozytor (aplikacje → strony → czas i tryb),
  Sesja (HUD z pozostałym czasem), Przerwa (okrąg oddechowy), Wolne, Bank, Statystyki (dzień /
  tydzień / miesiąc, heatmapa, top blokad, focus score), Ustawienia (8 zakładek), Dziennik.

Presety domyślne: *Matura — matematyka*, *Programowanie*, *Czytanie* (zakładane przy pierwszym
uruchomieniu). Możesz je edytować i dodawać własne.

## 5. Wybieranie aplikacji i stron

Kreator sesji (**KOMPOZYTOR**) składa plan w trzech krokach: aplikacje → strony → czas i tryb.
Kroki 1 i 2 nie wymagają wpisywania nazw — Cisza podpowiada gotowe katalogi, a własne pozycje
możesz dodać ręcznie.

**Krok 1 — aplikacje.** Cisza skanuje cztery źródła: skróty w Menu Start (`.lnk` → docelowy
`.exe`), aplikacje ze Sklepu (`Get-StartApps` + manifesty AppX), klucze `Uninstall` rejestru
oraz aktualnie uruchomione procesy z widocznym oknem. Instalatory, aktualizatory, przystawki MMC,
usługi w tle i narzędzia systemowe są odfiltrowane — zostają realne aplikacje użytkownika
(zwykle ok. 100 pozycji, zależnie od instalacji). Skan trwa kilka–kilkanaście sekund i leci
w tle przez `QThreadPool`, więc okno się nie zamraża; wynik trafia do cache
`%LOCALAPPDATA%\Cisza\apps_cache.json` na 24 h, a **ODŚWIEŻ LISTĘ** wymusza pełny skan (przycisk
zmienia się wtedy na `SKANUJĘ…`). Lista to przewijalna siatka kafli z ikonami, z wyszukiwarką
(filtrowanie po 250 ms), zakresem **WSZYSTKIE / OTWARTE TERAZ / MENU START / SKLEP**, przyciskiem
**WYCZYŚĆ** i licznikiem **ZNALEZIONO**. Zaznaczone aplikacje widać jako chipy (klik usuwa),
a **ZAPISZ JAKO DOMYŚLNE** utrwala wybór jako reguły `STUDY` w bazie (`save_app_selection`);
dopasowanie działa po nazwie procesu, np. `Kalkulator` → `calculatorapp.exe`.

**Krok 2 — strony.** Zakładka **NAUKA** grupuje katalog w kategoriach (nauka, szkoła, matura,
języki, kod, muzyka, narzędzia), a **BLOKOWANE** w kategoriach rozpraszaczy (rozrywka, social
media, wiadomości, zakupy). W każdej zakładce jest wyszukiwarka, pole **DODAJ HOST** na własny
wpis (np. `docs.python.org`), a w NAUCE dodatkowo pole przedmiotu z podpowiedziami po tematach
(`subject_suggestions`, np. `matematyka`). Zapis **ZAPISZ JAKO DOMYŚLNE** działa różnie dla obu
zakładek:

* **NAUKA** → hosty trafiają do profili stron kategorii `STUDY` (lista dozwolonych w nauce);
* **BLOKOWANE** → hosty dopisują się do `settings.network.blocklist`, czyli listy rozpraszaczy
  blokowanych przez proxy/`hosts` — **nie** do allowlisty. `clear_blocklist` przywraca domyślne
  blokady z `config.DEFAULT_BLOCKLIST`.

Krok 3 ustala czas i tryb (pomodoro, przerwy, NAUKA/WOLNY) — szczegóły w sekcji 6. Wybrany plan
możesz zapisać jako preset polem „nazwa presetu" + **ZAPISZ PRESET** i wczytać go później jednym
kliknięciem (presety pojawiają się też na ekranie startowym jako szybki start).

| Element | Krok | Działanie |
| --- | --- | --- |
| **ODŚWIEŻ LISTĘ** | 1 | pełny skan katalogu aplikacji (wymusza `force`, w tle), zapis do cache na 24 h |
| wyszukiwarka | 1, 2 | filtruje listę po nazwie (krok 1 także po nazwie procesu, krok 2 po hoście), opóźnienie 250 ms |
| **WSZYSTKIE / OTWARTE TERAZ / MENU START / SKLEP** | 1 | zawęża listę do wybranego źródła |
| **WYCZYŚĆ** / **ZNALEZIONO: N** | 1 | czyści zaznaczenie / liczba pozycji na liście |
| chipy | 1, 2 | zaznaczone pozycje; klik usuwa je z wyboru |
| **ZAPISZ JAKO DOMYŚLNE** | 1 | `save_app_selection` → reguły `STUDY` dla procesów |
| zakładki **NAUKA / BLOKOWANE** | 2 | katalog stron dozwolonych i rozpraszaczy |
| **DODAJ HOST** | 2 | ręczny host spoza katalogu |
| pole przedmiotu + podpowiedzi | 2 (NAUKA) | `subject_suggestions` dla matematyki, fizyki itd. |
| **ZAPISZ JAKO DOMYŚLNE** | 2 | `save_site_selection` z kategorią `STUDY` albo `BLOCKED` |
| ikony aplikacji | Ustawienia → Wygląd | `ui.color_app_icons` (domyślnie wyłączone = odbarwione) i `ui.app_icon_size` (24–64 px) |

Ikony są domyślnie odbarwiane do skali szarości (`.lnk`/`.exe`/`.ico` przez powłokę Windows,
`.png` z pakietów AppX czytane wprost); gdy ikony nie da się wyciągnąć, Cisza rysuje monogram
z tokenów motywu. Kolor włącza się wyłącznie świadomie przełącznikiem `ui.color_app_icons`,
a rozmiar ustawia `ui.app_icon_size`. Reszta interfejsu pozostaje czarno-biała.

## 6. Tryby pracy

### 6.1. Tryb sesji

| Tryb | Opis |
| --- | --- |
| `STUDY` | Pomodoro: nauka → przerwa (co 4. pomodoro długa przerwa). Domyślnie 25 / 5 / 15 min |
| `FREE` | "Wolne" opłacone minutami z banku; licznik schodzi z salda, reszta wraca do banku |

### 6.2. Tryb blokady (`LockConfig.mode`)

| Tryb | Zachowanie |
| --- | --- |
| `soft` | Blokada aplikacji i stron; pasek zadań zostaje widoczny, ale tylko z dozwolonymi aplikacjami (`taskbar_mode = "keep"` zostawia go całkiem bez zmian), bez zmiany tapety i skrótów — najłagodniejszy |
| `hard` | + pasek zadań widoczny, ale tylko z dozwolonymi aplikacjami, czarna tapeta, blokada skrótów, wyciszone toasty |
| `hardcore` | Jak `hard`, dodatkowo zaostrzone zasady wyjścia (kombinacja + PIN; przy wyłączonym „wyjście awaryjne wymaga PIN-u" działa natychmiastowy bezpiecznik awaryjny), limit `hardcore_max_minutes` (domyślnie 180 min) |

### 6.2.1. Pasek zadań w sesji (`LockConfig.taskbar_mode`)

Skróty są w sesji zablokowane, więc ukrycie paska zadań odcinało też dozwolone aplikacje —
nie było jak kliknąć Edge'a ani Notatnika. Dlatego od tej wersji pasek **zostaje na ekranie**,
ale pokazuje wyłącznie przyciski aplikacji z planu sesji:

| Tryb | Zachowanie |
| --- | --- |
| `filtered` (domyślny) | pasek widoczny; przyciski okien spoza allowlisty są usuwane (`ITaskbarList::DeleteTab`), po sesji wracają |
| `hide` | pasek całkowicie ukryty (dawne zachowanie `hard`/`hardcore`); okno Ciszy wchodzi na pełny ekran |
| `keep` | pasek nietknięty — bez filtrowania i bez ukrywania (sensowne w `soft`) |

Wybór jest w Ustawieniach → **Blokada** → **PASEK ZADAŃ W SESJI**. Pusta lista aplikacji
w kroku 1 kreatora nie filtruje niczego (tak jak guard procesów nie startuje przy pustej
liście), a procesy systemowe (`SYSTEM_SAFE`, m.in. `explorer.exe`) i okna samej Ciszy
zawsze zostają na pasku. Stan filtra jest zapisywany w
`%LOCALAPPDATA%\Cisza\backup\taskbar_filter.json`, więc przyciski wracają także po crash
GUI — przez watchdog, `python tools\restore.py --panic` albo przy następnym lockdownie.

### 6.3. Tryb sieci (`NetworkConfig.mode`)

| Tryb | Opis |
| --- | --- |
| `allowlist` | Przechodzi tylko lista dozwolonych hostów; reszta trafia na czarno-białą stronę blokady |
| `blocklist` | Blokuje listę rozpraszaczy (YouTube, social media itd.) |
| `off` | Sieć nietknięta |

Domeny z listy bezpieczeństwa (`SAFE_HOSTS`: Windows Update, serwery czasu, `localhost`)
nigdy nie są blokowane.

### 6.4. Ekonomia banku

* Za ukończone pomodoro: `earned = floor(sekundy × 0.5 / 15) × 15` (ulga 0,5, zaokrąglenie do 15 s).
* Limit dzienny: domyślnie 60 minut; nadwyżka trafia do `capped` i przepada.
* Minuty wygasają po 7 dniach (TTL), wydawane są FIFO (najstarsze pierwsze).
* Przerwane pomodoro nie zarabia, a przy `abort_penalty_minutes > 0` zabiera minuty z banku.
* Seria dni rośnie, gdy danego dnia uzbierasz co najmniej `streak_min_study_minutes`.

## 7. Struktura projektu

```text
Productivity/
├─ run.pyw                     # launcher bez okna konsoli
├─ requirements.txt
├─ docs/
│  ├─ INTERFACES.md            # zamrożone kontrakty modułów (źródło prawdy API)
│  ├─ DESIGN.md                # uzasadnienie decyzji projektowych
│  ├─ README.md                # ten plik
│  └─ SAFETY.md                # wyjścia awaryjne, restore, tryb safe
├─ focuslock/
│  ├─ __main__.py              # CLI: GUI, --helper, --watchdog, --restore, --safe
│  ├─ app.py                   # okno główne, spinanie ekranów, start aplikacji
│  ├─ controller.py            # spinanie modelu, widoku i blokad
│  ├─ helperclient.py          # klient RPC do procesu pomocniczego
│  ├─ config.py                # Settings + presety domyślne
│  ├─ store.py                 # SQLite: sesje, zdarzenia, bank, statystyki
│  ├─ economy.py               # bank minut: zarabianie, wydawanie, TTL, seria
│  ├─ session.py, timer.py     # FSM sesji i monotoniczny licznik
│  ├─ presets.py               # presety w Store
│  ├─ recovery.py              # restore_everything (używane przez watchdog i restore.py)
│  ├─ lockstate.py             # stan lockdownu na dysku (odtwarzanie po crashu)
│  ├─ paths.py                 # %LOCALAPPDATA%\Cisza + CISZA_DATA_DIR
│  ├─ ipc.py                   # RPC po nazwanym potoku (GUI ↔ helper)
│  ├─ helper.py                # proces uprzywilejowany (UAC)
│  ├─ appcatalog.py            # katalog aplikacji: Menu Start, Sklep, rejestr, otwarte procesy
│  ├─ sitecatalog.py           # katalog stron: kategorie nauki i rozpraszaczy
│  ├─ block/
│  │  ├─ processes.py          # reguły dopasowania + ProcessGuard
│  │  ├─ launchwatch.py        # WMI Win32_ProcessStartTrace (fallback: odpytywanie)
│  │  ├─ hosts.py              # sekcja CISZA-BEGIN/END w pliku hosts
│  │  ├─ proxy.py              # proxy HTTP/CONNECT z allowlistą (127.0.0.1)
│  │  ├─ firewall.py           # reguły netsh dla przeglądarek
│  │  └─ networklock.py        # spięcie proxy + hosts + zapory
│  ├─ shell/
│  │  ├─ desktop.py            # tapeta i ikony pulpitu
│  │  ├─ taskbar.py            # ukrywanie/pokazywanie paska zadań
│  │  ├─ taskbarfilter.py      # pasek zadań tylko z przyciskami dozwolonych aplikacji
│  │  ├─ hotkeys.py            # blokada Win/Alt+Tab/Alt+F4 + wyjście awaryjne
│  │  ├─ overlay.py            # czarne nakładki topmost (PyQt6)
│  │  ├─ dnd.py                # toasty, dźwięk, blokada uśpienia
│  │  ├─ watchdog.py           # heartbeat + auto-restore po crashu
│  │  └─ elevate.py            # UAC, uruchomienie helpera
│  └─ ui/
│     ├─ theme.py              # tokeny, typografia, QSS (tylko szarości)
│     ├─ icons.py              # ikony aplikacji (ekstrakcja + odbarwianie)
│     ├─ widgets/, screens/, charts.py, tray.py
├─ tests/                      # pytest (jednostkowe + integration/)
└─ tools/
   ├─ mono_lint.py             # lint: żadnych nie-szarych kolorów
   ├─ build_exe.py             # PyInstaller: dist\Cisza.exe
   ├─ install_autostart.py     # skrót w Startup / zadanie w Harmonogramie
   └─ restore.py               # ręczne wyjście awaryjne (--panic)
```

## 8. Testy i kontrola jakości

```bat
python -m pytest tests/ -q                     :: całość
python -m pytest tests/integration -q          :: pełny obieg na CISZA_DRY_RUN
python tools\mono_lint.py                      :: 0 = brak nie-szarych kolorow
python tools\mono_lint.py --json               :: raport maszynowy
```

Zasady, których pilnują testy:

* żaden plik UI nie zawiera koloru spoza skali szarości (użyj tokenów z `focuslock/ui/theme.py`);
* moduły poza `focuslock/ui/**`, `focuslock/shell/overlay.py` i warstwą GUI `focuslock/app.py`
  nie importują PyQt;
* testy nie dotykają prawdziwego `%LOCALAPPDATA%\Cisza` ani systemowego pliku `hosts`
  (używają `CISZA_DATA_DIR`, `tmp_path` i `Store(memory=True)`);
* operacje systemowe w `dry_run=True` zwracają tylko raport i nic nie zmieniają.

## 9. Budowanie `Cisza.exe`

```bat
python tools\build_exe.py                :: build + dist\Cisza.exe
python tools\build_exe.py --dry-run      :: pokaz plan bez uruchamiania PyInstallera
python tools\build_exe.py --onedir       :: katalog zamiast jednego pliku
python tools\build_exe.py --console      :: zachowaj konsole (diagnostyka)
python tools\build_exe.py --no-icon      :: bez generowania ikony
python tools\build_exe.py --with-matplotlib  :: nie wykluczaj matplotlib
python tools\build_exe.py --name MojaCisza   :: inna nazwa pliku
python tools\build_exe.py --skip-verify      :: pomiń test .exe po budowie
```

Po zbudowaniu skrypt sam uruchamia `dist\Cisza.exe --restore --dry-run` (na danych w
`build\verify-data`) i wymaga kodu wyjścia 0 oraz braku komunikatu `No module named`;
jeśli coś brakuje, build kończy się kodem 1 i wypisuje winowajcę. To zabezpiecza przed
regresją importów dynamicznych w zamrożonej aplikacji. Ponieważ `.exe` powstaje bez konsoli
(`--noconsole`, `sys.stdout is None`), raport trybów CLI trafia także do pliku wskazanego
zmienną `CISZA_REPORT_FILE` — z niego czyta go weryfikacja buildu i z niego możesz skorzystać
diagnostycznie, np. `set CISZA_REPORT_FILE=raport.txt && dist\Cisza.exe --restore --dry-run`.

Skrypt generuje spec PyInstallera (`onefile`, `--noconsole`, nazwa `Cisza`), rysuje ikonę
`.ico` w skali szarości i uruchamia build. Wynik: `dist\Cisza.exe` (ikona `dist\cisza.ico`).
Uwagi:

* **jednoplikowy `.exe` rozpakowuje się do `%TEMP%`** — jeśli na danej maszynie
  PyInstaller nie potrafi utworzyć tam katalogu `_MEI…`, zobaczysz okno
  „Could not create temporary directory!" i aplikacja nie wstanie; wtedy użyj
  `python tools\build_exe.py --onedir` (albo `pythonw run.pyw`). Build katalogowy
  (`dist\Cisza\Cisza.exe`) nie rozpakowuje niczego do `%TEMP%`, więc działa zawsze
  i startuje szybciej;

* pierwszy build trwa kilka minut, `build/` i `dist/` są jednorazowe;
* skrypt używa `--collect-submodules focuslock`, bo `recovery.py`, `helper.py`
  i `controller.py` importują moduły dynamicznie (`importlib.import_module`); bez tego zamrozony
  `.exe` nie potrafi np. przywrócić paska zadań ("No module named 'focuslock.shell.taskbar'");
* plik `.exe` nie omija UAC — blokada `hosts` i zapory nadal wymaga helpera;
* test bez budowania: `python -c "import focuslock.ui.theme"`.

## 10. Autostart

```bat
python tools\install_autostart.py              :: skrot w folderze Startup
python tools\install_autostart.py --task       :: dodatkowo zadanie w Harmonogramie zadan
python tools\install_autostart.py --task-only  :: tylko zadanie (bez skrotu)
python tools\install_autostart.py --status     :: co jest zainstalowane
python tools\install_autostart.py --remove     :: usuniecie skrotu i zadania
python tools\install_autostart.py --dry-run    :: plan bez zmian w systemie
python tools\install_autostart.py --exe dist\Cisza.exe   :: wskaz konkretny plik
```

Autostart nie wymaga administratora (zakładanie zadania w Harmonogramie może o niego poprosić).
W Ustawieniach Ciszy jest przełącznik `SystemConfig.autostart`, który tylko steruje tym samym
wpisem.

## 11. Ograniczenia i znane problemy

* **`Ctrl+Alt+Del` jest nieblokowalny** i to celowe — to jedyne pewne wyjście z hardcore.
* **Blokada proxy nie robi MITM**: ruch HTTPS idzie przez `CONNECT`, więc filtrowana jest nazwa
  hosta, a nie zawartość stron.
* **Wyciszenie dźwięku jest best-effort** — bez `comtypes` może zwrócić `ok=False` z ostrzeżeniem.
* **WMI (`Win32_ProcessStartTrace`) bywa niedostępne** — wtedy watcher startów przechodzi na
  odpytywanie (wolniejsze, ale działa).
* **Tryby `hard`/`hardcore` zmieniają wygląd systemu** (pasek zadań, tapeta). Po nieczystym
  zamknięciu przywróci je watchdog albo `python tools\restore.py --panic`.
* **Filtrowanie paska zadań** usuwa przyciski okien najwyższego poziomu (`ITaskbarList`).
  Aplikacje ze Sklepu (`ApplicationFrameHost.exe`) są rozpoznawane po oknie potomnym; gdy
  Windows odmówi usunięcia przycisku, Cisza awaryjnie ukrywa samo okno i przywraca je po
  sesji (szczegół w raporcie: `taskbarfilter:hide_window:0x…`).
* **Jedno konto / jedna sesja**: dane i token helpera są per użytkownik.
* **Reguły zapory (`block_browser_direct`) wymagają administratora** i — jeśli zostaną po
  nieczystym zamknięciu — potrafią zablokować przeglądarki na stałe (`ERR_NETWORK_ACCESS_DENIED`).
  Konto bez uprawnień administratora **nie usunie ich samo**, dlatego ta opcja jest domyślnie
  **wyłączona**, a w katalogu projektu jest `napraw-internet.cmd` (kliknij prawym →
  „Uruchom jako administrator”). Gdy aplikacja wykryje takie reguły przy starcie, pokazuje
  ostrzeżenie z tą instrukcją.
* **Windows tylko**: powłoka, rejestr i `netsh` nie mają odpowiedników na innych systemach.
* Testy i `dry_run` nie blokują systemu, więc nie zastąpią próby na żywo — ale próbę na żywo
  rób po zapisaniu pracy i po przeczytaniu [SAFETY.md](SAFETY.md).

## 12. Szybka pomoc

| Objaw | Co zrobić |
| --- | --- |
| Nie znam PIN-u, GUI nie odpowiada | `Ctrl+Alt+Del` → zakończ `pythonw.exe` → `python tools\restore.py --panic` |
| Pasek zadań nadal ukryty | `python tools\restore.py --panic` (jako administrator) |
| Przeglądarka "nie ma internetu" | `python tools\restore.py --panic` — cofa proxy i `hosts`; na koncie bez admina użyj `napraw-internet.cmd` (prawy klik → Uruchom jako administrator), bo reguły zapory `CiszaBlock-*` usuwa tylko administrator |
| Chcę tylko obejrzeć aplikację | `set CISZA_DRY_RUN=1 && python run.pyw` |
| Nie widzę paska zadań w sesji | Ustawienia → Blokada → **PASEK ZADAŃ W SESJI** = `TYLKO DOZWOLONE APLIKACJE` (domyślne); `UKRYJ CAŁKIEM` to dawne zachowanie `hard`/`hardcore` |
| Dozwolona aplikacja ma zniknięty przycisk na pasku | Sprawdź, czy proces jest na liście (np. `msedge.exe`), i odśwież listę w kroku 1 kreatora; przyciski wracają po zakończeniu sesji |
| Coś jest nie tak z ustawieniami | `pythonw run.pyw --safe` |
| Brakuje mojej aplikacji w kroku 1 | **ODŚWIEŻ LISTĘ** (pełny skan); w razie potrzeby usuń `%LOCALAPPDATA%\Cisza\apps_cache.json` i odśwież ponownie |

Dokumenty powiązane: [SAFETY.md](SAFETY.md) · [INTERFACES.md](INTERFACES.md).
