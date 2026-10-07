# Lupa — specyfikacja aplikacji

> Wersja 0.2.1 (beta) · październik 2026 · © Smart Pixel · licencja MIT

## 1. Czym jest Lupa

**Lupa to lokalna, semantyczna wyszukiwarka plików na macOS.** Pozwala znaleźć plik po **opisie jego zawartości**,
a nie tylko po nazwie, np.:

- „zdjęcie szatni kontenerowej przy boisku”
- „faktura za transport 2025”
- „logo klubu na przezroczystym tle”
- „zrzut ekranu strony z formularzem rejestracji”

Rozumie **tekst dokumentów, wygląd obrazów, klatki filmów i dźwięk**, bo zamienia każdy plik w wektor
liczb (*embedding*) w jednej wspólnej przestrzeni znaczeń. Zapytanie tekstowe (albo przeciągnięty obraz)
trafia do tej samej przestrzeni, a wyniki to pliki o najbliższym znaczeniu.

**Wszystko działa lokalnie na MacBooku.** Żaden plik ani zapytanie nie jest wysyłane do internetu.
Internet był potrzebny tylko raz, do pobrania modelu.

## 2. Do czego służy

| Zastosowanie | Przykład |
|---|---|
| Szukanie pliku, którego nazwy nie pamiętasz | „oferta na szatnie dla klubu z Mazowsza” |
| Szukanie zdjęć po tym, co na nich jest | „piłkarze w czerwonych strojach na sztucznej murawie” |
| Szukanie po obrazie | przeciągasz zdjęcie → Lupa pokazuje podobne grafiki |
| Porządkowanie | kolorowe tagi, ulubione, zapisane kolekcje (np. „Faktury”, „Logotypy”) |
| Szybki przegląd | siatka miniatur, podgląd, pełny ekran, filtry typu/daty/folderu |
| Odzyskiwanie miejsca | zakładka **Porządki**: cache, resztki po aplikacjach, duplikaty, stare instalatory — bezpiecznie, przez Kosz |
| Praca na NAS | indeks i miniatury zostają lokalnie, więc przeglądanie działa nawet przy odłączonym dysku sieciowym |

## 3. Lokalizacje plików

| Co | Ścieżka |
|---|---|
| **Projekt (kod źródłowy)** | folder, do którego sklonowano repozytorium (dalej `<projekt>`) |
| **Aplikacja macOS** | `/Applications/Lupa.app` |
| Konfiguracja | `<projekt>/lupa.toml` (tworzona z `lupa.example.toml`) |
| Indeks (baza SQLite) | `<projekt>/data/lupa.db` |
| Miniatury (cache) | `<projekt>/data/thumbs/` |
| Logi | `<projekt>/data/server.log`, `data/indexer.log` |
| Model AI (wagi, ~1,4 GB) | `~/.cache/huggingface/hub/models--google--embeddinggemma-2/` |
| Środowisko Pythona (~1,2 GB) | `<projekt>/.venv/` |
| Element logowania | Ustawienia systemowe → Ogólne → Rzeczy otwierane podczas logowania → *Lupa* |
| Uprawnienie | Ustawienia → Prywatność i ochrona → Pełny dostęp do dysku → *Lupa* |

### Struktura projektu

```
<projekt>/
├── install.sh / uninstall.sh   # instalacja i odinstalowanie
├── lupa.example.toml      # wzór konfiguracji (kopiowany do lupa.toml)
├── README.md / README.pl.md     # opis (EN / PL)
├── docs/SPECYFIKACJA.md   # ten dokument
├── lupa/                  # silnik (Python)
│   ├── common.py          #   konfiguracja, schemat bazy, typy plików
│   ├── embedder.py        #   obsługa modelu EmbeddingGemma 2 (GPU/CPU)
│   ├── extract.py         #   wyciąganie treści: tekst, obrazy, klatki wideo, audio, miniatury
│   ├── indexer.py         #   indeksowanie: skan → nazwy → miniatury → analiza treści
│   ├── search.py          #   wyszukiwanie wektorowe + słowa kluczowe, ranking
│   ├── cleanup.py         #   Porządki: skan dysku lokalnego, kategorie, bezpieczne przenoszenie do Kosza
│   └── server.py          #   serwer HTTP/API (localhost:7766)
├── static/                # interfejs (HTML/CSS/JS bez frameworków)
│   ├── index.html
│   ├── style.css
│   ├── app.js
│   └── cleanup.js         #   zakładka Porządki
├── macapp/                # natywna aplikacja macOS
│   ├── main.swift         #   okno WebKit, skrót ⌥⌘L, uruchamianie silnika
│   ├── build.sh           #   kompilacja i instalacja do /Applications
│   ├── make_icon.py       #   generator ikony
│   └── Lupa.icns
├── raycast/lupa.sh        # komenda Raycast (otwiera Lupa.app)
├── scripts/serve.sh       # start serwera (uruchamiany przez Lupa.app)
└── data/                  # indeks, miniatury, logi (można usunąć, odbudują się)
```

## 4. Architektura

```
┌──────────────────────────────┐
│  Lupa.app (Swift + WebKit)   │  okno, ikona w Docku, skrót ⌥⌘L, element logowania
│  uruchamia i pilnuje silnika │
└──────────────┬───────────────┘
               │ http://localhost:7766  (tylko lokalnie)
┌──────────────▼───────────────┐
│  Serwer (Python · FastAPI)   │  wyszukiwanie, miniatury, podgląd plików, tagi, kolekcje
│  zapytania tekstowe → CPU    │  ~60 ms na zakodowanie zapytania
│  wektory w pamięci (NumPy)   │
└──────────────┬───────────────┘
               │ uruchamia co 3 h / przyciskiem / po podłączeniu NAS
┌──────────────▼───────────────┐
│  Indeksator (Python)         │  1. skan folderów (najpierw dysk lokalny, potem NAS)
│  model na GPU (Apple MPS)    │  2. wektory nazw i folderów
│  niski priorytet (nice 10)   │  3. szybkie miniatury (CPU)
│                              │  4. analiza treści: obrazy, PDF, Office, wideo, audio
└──────────────┬───────────────┘
               │
┌──────────────▼───────────────┐
│  SQLite (WAL) + miniatury    │  pliki, wektory, pełnotekstowy indeks FTS5, tagi, kolekcje
└──────────────────────────────┘
```

Silnik uruchamia Lupa.app jako swój proces potomny. Dzięki temu uprawnienie **Pełny dostęp do dysku**
nadane „Lupie” obejmuje też indeksator (macOS chroni Pobrane, Biurko i Dokumenty).
Zamknięcie okna (⌘W) zostawia Lupę w tle, a ⌘Q zatrzymuje ją razem z silnikiem.

## 5. Model AI

| Parametr | Wartość |
|---|---|
| Model | **Google EmbeddingGemma 2** (`google/embeddinggemma-2`, Hugging Face), oparty na Gemma 4 |
| Licencja | Apache 2.0 (darmowy, także do użytku komercyjnego) |
| Rozmiar | 740 mln parametrów: tekst 270M + wizja 170M + audio 300M |
| Modalności | tekst (w tym kod), obrazy, wideo (klatki), audio |
| Języki | 100+ (w tym polski) |
| Kontekst | 8192 tokeny |
| Wymiar wektora | 768, w Lupie **przycięty do 256** (Matryoshka Representation Learning, 3× mniej miejsca, minimalna utrata jakości) |
| Precyzja | bfloat16 na GPU (Apple Silicon / MPS), float32 na CPU; **nigdy float16** (przepełnienia) |

### Jak model jest używany

| Zadanie | Gdzie działa | Szczegóły |
|---|---|---|
| Nazwa + folder każdego pliku | GPU (indeksator) | `title: <nazwa> \| text: <typ> w folderze: <ścieżka>` |
| Tekst dokumentów | GPU (indeksator) | do 4 fragmentów × ~1800 znaków na plik |
| Obrazy, skany PDF, projekty graficzne | GPU (indeksator) | 140 tokenów na obraz (ustawienie `image_tokens`) |
| Wideo | GPU (indeksator) | 4 klatki (8%, 35%, 62%, 90% długości); wynik przewija film do pasującej klatki |
| Audio | GPU (indeksator) | 30-sekundowy fragment, 16 kHz mono |
| Zapytanie tekstowe | **CPU** (serwer, bfloat16) | prompt `task: search result \| query:`, ~50 ms, nie czeka na GPU |
| Zapytanie obrazem | GPU (serwer, ładowany przy pierwszym użyciu) | |

### Wydajność na MacBook Pro M2 Pro, 16 GB

| Operacja | Tempo |
|---|---|
| Skan folderów (dysk lokalny) | ~100 tys. plików / 8 s |
| Wektory nazw | ~80–240 plików/s |
| Miniatury (CPU, 6 wątków) | kilkadziesiąt plików/s |
| Analiza obrazów: 70 / **140** / 280 tokenów | 7,8 / **3,7** / 1,6 obrazu/s (zgodność ze 280: 95% / 97% / 100%) |
| Zapytanie (300 tys. wektorów, w trakcie indeksowania) | ~0,09 s |

## 6. Wyszukiwanie i ranking

1. **Semantycznie:** zapytanie jest porównywane (iloczyn skalarny) ze wszystkimi wektorami w pamięci.
2. **Normalizacja per rodzaj wektora:** wyniki dla nazw, tekstu, obrazów i audio są osobno przeliczane na
   *z-score*, bo surowe podobieństwa obraz↔tekst (~0,6) są niższe niż tekst↔tekst (~0,85).
   Waga dopasowania samej nazwy wynosi 0,8.
3. **Słowa kluczowe:** SQLite FTS5 (bez polskich znaków diakrytycznych) daje premię za trafienie
   w nazwę lub folder (+1,6) i w treść (+0,8).
4. Dla każdego pliku liczy się najlepsze dopasowanie. Lupa pokazuje też, *dlaczego* plik pasuje
   (nazwa / treść / obraz / dźwięk / słowo kluczowe).
5. **„Podobne”** i **szukanie obrazem** porównują tylko wektory tego samego rodzaju (obraz z obrazem).
6. Filtry (typ, data, dysk, folder, tagi) działają przed rankingiem.

## 7. Obsługiwane pliki

| Typ | Co jest analizowane | Narzędzie |
|---|---|---|
| JPG, PNG, WebP, HEIC, GIF, TIFF… | wygląd obrazu | Pillow, pillow-heif |
| SVG, PSD, AI, EPS, RAW (CR2, NEF, ARW, DNG…), Sketch, Affinity, Pages/Keynote/Numbers | wygląd (render Quick Look) | `qlmanage -t` |
| PDF | tekst do 10 stron; skany bez tekstu → obraz 1. strony | PyMuPDF |
| DOCX / PPTX / XLSX | tekst, tabele, slajdy, arkusze | python-docx, python-pptx, openpyxl |
| DOC, RTF, ODT | tekst | macOS `textutil` |
| TXT, MD, CSV, kod, HTML, ipynb | tekst | wbudowane |
| MP4, MOV, MKV… | 4 klatki + długość | ffmpeg / ffprobe |
| MP3, WAV, M4A, FLAC… | fragment dźwięku | ffmpeg |
| Pozostałe (archiwa, pakiety, inne) | tylko nazwa i folder | — |

Pomijane: foldery systemowe i techniczne (`~/Library` poza iCloud Drive, `node_modules`, `.git`,
`dist`, cache aplikacji, kosz Synology `#recycle`, `@eaDir`…), pliki ukryte, pliki iCloud niepobrane na dysk.
Pełna lista jest w `lupa.toml`.

## 8. Funkcje interfejsu

- siatka miniatur (regulowana wielkość) i widok listy; miniatury plików przezroczystych na szachownicy
- panel podglądu: obraz, odtwarzacz wideo/audio, PDF, tekst dokumentu, metadane
- pełny ekran z przewijaniem strzałkami (`Q`)
- najechanie na film odtwarza podgląd
- filtry: typ pliku, czas, dysk, skróty folderów (Pobrane, Biurko, Dokumenty, iCloud Drive), konkretny folder, tagi
- sortowanie: trafność, data modyfikacji/utworzenia, nazwa, rozmiar, typ
- tagi z wyborem koloru, zmianą nazwy i tagowaniem wielu plików naraz; ulubione
- kolekcje, czyli zapisane wyszukiwania (10 gotowych: Faktury, Umowy, Oferty, Logotypy, Zrzuty ekranu…)
- „Podobne” do pliku, szukanie obrazem (przeciągnij, wklej ⌘V lub przycisk 🖼)
- otwieranie pliku i pokazywanie w Finderze
- status indeksowania z postępem, tempem, czasem do końca i ostrzeżeniem o braku uprawnień
- tryb jasny i ciemny według systemu

Pełna lista skrótów klawiszowych jest w `README.md`.

## 9. Porządki — czyszczenie dysku lokalnego

Zakładka **Porządki** pomaga utrzymać wolne miejsce na dysku systemowym (Macintosh HD). Skanuje **tylko dysk lokalny**
(folder domowy i `/Applications`), nigdy plików systemowych macOS ani dysków sieciowych. Pełny skan trwa ok. 15–20 s.

### Jak odróżnia ważne pliki od śmieci (warstwy, od najpewniejszej)

| Warstwa | Metoda | AI? |
|---|---|---|
| 1. Twarde zakazy | `/System`, `/usr`, `/Library`, pęk kluczy, Poczta, Wiadomości, iCloud Drive, folder Lupy i model AI są nietykalne; dozwolone tylko `/Users/…` i `/Applications/…` | nie |
| 2. Kategorie bezpieczne z definicji | cache aplikacji, cache narzędzi (npm, pip, uv, Gradle, Xcode), logi, instalatory `.dmg/.pkg` — aplikacje odtwarzają je same | nie |
| 3. Resztki po odinstalowanych aplikacjach | foldery w `~/Library` (Application Support, Containers, Group Containers, HTTPStorages, WebKit) porównywane z identyfikatorami (bundle ID) i nazwami ~450 zainstalowanych aplikacji; do tego brak zmian od ≥30 dni | nie |
| 4. Faktyczne użycie | data ostatniego otwarcia z macOS (Spotlight `kMDItemLastUsedDate`) dla aplikacji, Pobranych i dużych plików | nie |
| 5. Doradca AI (planowane) | Gemma 4 lokalnie (Ollama) opisuje nieznane foldery z poziomem pewności — tylko jako opis, nigdy decyzja | tak |

### Kategorie

| Kategoria | Ryzyko | Domyślnie zaznaczone |
|---|---|---|
| 🧹 Cache aplikacji | bezpieczne | tak (oprócz aplikacji, które są właśnie otwarte) |
| 🛠️ Cache narzędzi programisty | bezpieczne | tak |
| 📜 Logi | bezpieczne | tak |
| 📦 Instalatory (.dmg, .pkg, .iso) | bezpieczne | starsze niż 30 dni |
| 👻 Resztki po odinstalowanych aplikacjach | sprawdź | nie |
| ⬇️ Stare pliki w Pobranych (nieotwierane 90+ dni) | sprawdź | nie |
| 👯 Duplikaty (identyczne co do bajtu, suma kontrolna BLAKE2) | sprawdź | tylko kopie w Pobranych/na Biurku lub z „(1)”, „kopia”; najlepsza kopia zawsze zostaje |
| 💤 Nieużywane aplikacje (180+ dni, pełne odinstalowanie) | sprawdź | nie |
| 🐘 Duże, dawno nieużywane pliki (200+ MB, rok) | uważaj | nie |
| 📱 Kopie zapasowe iPhone'a / iPada | uważaj | nie |

### Odinstalowywanie aplikacji („Nieużywane aplikacje”)

Lupa nie wyrzuca samego pliku `.app`. Dla każdej aplikacji rozpoznaje **właściwą metodę**:

| Sytuacja | Metoda w Lupie |
|---|---|
| Zwykła aplikacja i aplikacje z App Store | **pełne odinstalowanie**: `.app` + jej dane z `~/Library` (Application Support, Caches, Containers, Preferences, Saved Application State, HTTPStorages, WebKit, Logs, LaunchAgents, Cookies) do Kosza; dopasowanie tylko po dokładnym identyfikatorze lub nazwie, nigdy wspólne foldery producenta |
| Aplikacje Adobe | blokada Kosza, przycisk **Otwórz Creative Cloud** |
| Aplikacja z własnym deinstalatorem | blokada Kosza, przycisk **Uruchom deinstalator** |
| Aplikacja z komponentami systemowymi (rozszerzenia systemowe, pomocnicy z uprawnieniami, usługi `LaunchDaemons` należące do tej aplikacji) | blokada Kosza, wskazówka, by użyć deinstalatora producenta |

Z listy „nieużywanych” wykluczane są aplikacje **uruchomione teraz**, **elementy logowania** i aplikacje startowane przez
**LaunchAgents/LaunchDaemons**, bo działają w tle i nie są „otwierane”, a mimo to są używane. Otwartej aplikacji nie da się odinstalować:
Lupa poprosi o jej zamknięcie.

### Bezpieczeństwo usuwania
- Nic nie jest kasowane: elementy trafiają do **Kosza przez Findera** (działa „Odłóż”). Miejsce zwalnia się po opróżnieniu Kosza
  (przycisk w zakładce, z potwierdzeniem).
- Serwer pozwala przenieść do Kosza **wyłącznie ścieżki z ostatniego skanu**, po ponownym sprawdzeniu zakazów.
- Każda operacja jest zapisywana w `data/cleanup_log.jsonl`.
- Lista „chronionych” (🛡 przy elemencie) — nigdy więcej nie będą proponowane.
- Przy pierwszym użyciu macOS pyta o zgodę na sterowanie Finderem.

### Systematyczność
- Automatyczny skan co 7 dni (`[cleanup] every_days`).
- Powiadomienie macOS, gdy wolne miejsce spadnie poniżej 15% (max raz dziennie), z informacją, ile można odzyskać.
- Opcja (domyślnie wyłączona): co tydzień automatycznie przenoś do Kosza cache i logi.
- Czerwony znacznik „!” na zakładce, gdy miejsca jest mało.

Przykład z pierwszego uruchomienia (MacBook Pro, 460 GB, 7% wolnego): **~52 GB do odzyskania**, w tym ~27 GB w kategoriach bezpiecznych.

## 10. Technologie

| Warstwa | Technologia |
|---|---|
| Model AI | EmbeddingGemma 2 przez **sentence-transformers 6.1** + **transformers 5.19** + **PyTorch 2.14** (backend MPS) |
| Silnik | Python 3.12, zarządzany przez **uv** |
| Serwer | **FastAPI** + Uvicorn, tylko `127.0.0.1:7766` |
| Baza | **SQLite** (WAL, FTS5), wektory jako float16 w BLOB-ach, wyszukiwanie w **NumPy** |
| Ekstrakcja | PyMuPDF, python-docx, python-pptx, openpyxl, Pillow, pillow-heif, ffmpeg, macOS Quick Look i textutil |
| Interfejs | czysty HTML/CSS/JavaScript (bez frameworków i bez internetu) |
| Aplikacja | Swift + AppKit + **WKWebView**, globalny skrót przez Carbon HotKey, podpis ad-hoc |

## 11. Bezpieczeństwo i prywatność

- Serwer nasłuchuje wyłącznie na `localhost`; sprawdza nagłówek `Host` (ochrona przed DNS rebinding).
- Akcje zmieniające stan (otwieranie plików, tagi) wymagają nagłówka `x-lupa` i właściwego `Origin`,
  więc obca strona w przeglądarce nie może ich wywołać.
- Brak telemetrii; model ładowany offline (`local_files_only`).
- Indeks nie usuwa wpisów z dysków offline ani z folderów, do których zabrakło uprawnień.

## 12. Konfiguracja (`lupa.toml`)

| Klucz | Znaczenie | Domyślnie |
|---|---|---|
| `roots` | indeksowane foldery (np. dodaj `/Volumes/NAS`) | `~` (folder domowy) |
| `places` | skróty w sekcji „Gdzie” | Pobrane, Biurko, Dokumenty, Obrazy, iCloud Drive |
| `reindex_every_hours` | automatyczne odświeżanie | 3 |
| `image_tokens` | szczegółowość analizy obrazów (70/140/280) | 140 |
| `dim` | wymiar wektorów (128–768) | 256 |
| `[cleanup]` | Porządki: interwał skanu, próg alertu, progi wieku/rozmiaru | 7 dni, 15%, 200 MB, rok |
| `exclude_paths`, `exclude_dirs`, `skip_exts`, `package_exts` | wykluczenia | patrz plik |

Zmiana `dim` wymaga przebudowy indeksu (usunięcie `data/`).

## 13. Zużycie zasobów (pomiar: MacBook Pro M2 Pro, 16 GB)

| Proces | RAM | Uwagi |
|---|---|---|
| Lupa.app (okno) | ~30 MB | |
| Serwer | ~0,7 GB | model zapytań na CPU (bfloat16) |
| Indeksator | ~3,3 GB (pomiar przy indeksowaniu nazw; zwalnia model podczas skanu NAS) | działa tylko podczas indeksowania, z niskim priorytetem |
| Dysk | model 1,4 GB · środowisko 1,2 GB · indeks + miniatury ~1–2 GB (zależnie od liczby plików) | |

## 14. Znane ograniczenia i plany

- Pierwsza analiza treści dużych bibliotek trwa godzinami (obrazy ~3,7/s); NAS przez SMB jest wolniejszy.
- Skany PDF są rozumiane wizualnie, bez OCR (planowany Tesseract).
- Brak wykrywania duplikatów (planowane; np. kopie w eksportach stron).
- Porządki: doradca Gemma 4 dla nieznanych folderów (planowane).
- Aplikacja jest podpisana ad-hoc; po przebudowie macOS może poprosić o ponowne nadanie uprawnień.

## 15. Źródła

- [EmbeddingGemma 2 — Google Blog](https://blog.google/innovation-and-ai/technology/developers-tools/embeddinggemma-2/)
- [EmbeddingGemma 2: The Developer Guide](https://developers.googleblog.com/embeddinggemma-2-the-developer-guide/)
- [google/embeddinggemma-2 — Hugging Face](https://huggingface.co/google/embeddinggemma-2)
