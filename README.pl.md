<p align="center">
  <img src="docs/icon.png" width="128" alt="Ikona Lupy">
</p>

<h1 align="center">Lupa</h1>

<p align="center">
  <b>Znajdź każdy plik na Macu, opisując go własnymi słowami.</b><br>
  Lokalna, prywatna wyszukiwarka plików oparta na modelu Google <b>EmbeddingGemma 2</b>.
</p>

<p align="center">
  <a href="README.md">🇬🇧 English</a> ·
  <a href="#instalacja">Instalacja</a> ·
  <a href="#funkcje">Funkcje</a> ·
  <a href="docs/SPECYFIKACJA.md">Pełna specyfikacja</a>
</p>

<p align="center">
  <img alt="macOS" src="https://img.shields.io/badge/macOS-13%2B-black?logo=apple">
  <img alt="Apple Silicon" src="https://img.shields.io/badge/Apple%20Silicon-zoptymalizowane-8e6cff">
  <img alt="Licencja MIT" src="https://img.shields.io/badge/licencja-MIT-blue">
  <img alt="Status" src="https://img.shields.io/badge/status-beta-orange">
</p>

---

Wpisz *„zdjęcie drużyny na sztucznej murawie”*, *„faktura za transport 2025”* albo *„logo na przezroczystym tle”*, a Lupa znajdzie właściwe pliki, nawet jeśli ich nazwy nic nie mówią. Rozumie treść dokumentów, wygląd obrazów, klatki filmów i dźwięk.

**Nic nie opuszcza Twojego Maca.** Bez chmury, bez konta, bez telemetrii. Internet jest potrzebny tylko raz, do pobrania modelu.

> **Wersja beta.** Lupa działa na co dzień, ale to młody projekt. Zgłoszenia błędów i pull requesty są mile widziane.

## Funkcje

- 🔎 **Szukanie po znaczeniu:** semantyczne i po słowach kluczowych, w nazwach, folderach, treści dokumentów, obrazach, klatkach wideo i audio.
- 🖼️ **Dla wzrokowców:** duże miniatury, szybki podgląd, pełny ekran (←/→), filmy odtwarzane po najechaniu i przewijane do pasującego momentu.
- 🧲 **Szukanie obrazem:** przeciągnij lub wklej zdjęcie, żeby znaleźć podobne. Przycisk **„Podobne”** działa dla każdego pliku.
- 🏷️ **Tagi i kolekcje:** kolorowe tagi (także dla wielu plików naraz), ulubione i zapisane wyszukiwania.
- 🎛️ **Filtry i sortowanie:** typ, data, dysk, skróty folderów (Pobrane, Biurko, Dokumenty, iCloud Drive), tagi.
- 🧹 **Porządki:** odzyskiwanie miejsca na dysku Maca. Lupa znajduje cache, logi, stare instalatory, resztki po odinstalowanych aplikacjach, identyczne duplikaty, nieużywane aplikacje i duże stare pliki, a zaznaczone przenosi do Kosza (zawsze da się je przywrócić).
- 🗄️ **Obsługa NAS:** indeks i miniatury zostają lokalnie, więc przeglądanie działa nawet przy odłączonym dysku sieciowym.
- 🍏 **Natywna aplikacja:** ikona w Docku, skrót **⌥⌘L**, autostart, tryb jasny i ciemny.
- ⌨️ **Skróty:** `/` szukaj · strzałki · `Spacja` podgląd · `Q` pełny ekran · `Enter` otwórz · `⌘Enter` pokaż w Finderze · `S` podobne · `T` taguj · `F` ulubione.

## Wymagania

- macOS 13+, zalecany **Apple Silicon** (na Macu z procesorem Intel też działa, ale wolniej).
- Około **3 GB** wolnego miejsca (model 1,4 GB + biblioteki 1,2 GB) plus miejsce na indeks.
- [Homebrew](https://brew.sh) i Xcode Command Line Tools. Instalator sam doinstaluje `uv` i `ffmpeg`.
- Zalecane 16 GB RAM.

## Instalacja

```bash
git clone https://github.com/Smart-Pixel-PL/lupa-search.git
cd lupa-search
./install.sh
```

Możesz też pobrać ZIP (zielony przycisk **Code**), rozpakować go i uruchomić `./install.sh` w rozpakowanym folderze.

**Na koniec nadaj Lupie dostęp do plików:** *Ustawienia systemowe → Prywatność i ochrona → **Pełny dostęp do dysku*** → ＋ → **Lupa**. Potem zamknij Lupę (⌘Q) i otwórz ją ponownie.

> Nie usuwaj folderu projektu po instalacji, bo Lupa.app uruchamia z niego swój silnik.

Odinstalowanie: `./uninstall.sh`

## Użytkowanie

Otwórz **Lupę** z Launchpada albo naciśnij **⌥⌘L**. Pierwsze indeksowanie rusza samo: najpierw skan i nazwy (po kilku minutach wszystko da się znaleźć po nazwie), potem miniatury, a na końcu analiza treści, która przy dużych zbiorach trwa kilka godzin. Indeksowanie działa z niskim priorytetem i można je wstrzymać w lewym dolnym rogu.

Konfiguracja jest w pliku `lupa.toml` w folderze projektu (powstaje z [`lupa.example.toml`](lupa.example.toml)). Ustawisz w nim foldery do indeksowania (np. dysk sieciowy `"/Volumes/NAS"`), skróty folderów, wykluczenia i jakość analizy obrazów.

## Porządki

Zakładka do utrzymania porządku na **dysku lokalnym**. Pliki systemowe macOS, pęk kluczy, Poczta, Wiadomości i iCloud Drive są nietykalne, a dyski sieciowe są pomijane. Nieużywane aplikacje są **prawidłowo odinstalowywane**: aplikacja trafia do Kosza razem ze swoimi danymi z `~/Library`. Aplikacje Adobe Lupa odsyła do Creative Cloud, a aplikacje z własnym deinstalatorem albo z komponentami systemowymi do deinstalatora producenta. Każda pozycja ma poziom ryzyka (bezpieczne / sprawdź / uważaj). Lupa niczego nie kasuje sama: zaznaczone elementy trafiają do Kosza przez Findera. Skan uruchamia się co tydzień, a przy wolnym miejscu poniżej 15% przychodzi powiadomienie. Opcjonalnie Lupa może co tydzień sama przenosić do Kosza cache i logi. Szczegóły są w [specyfikacji](docs/SPECYFIKACJA.md#9-porządki--czyszczenie-dysku-lokalnego).

## Technologia w skrócie

- **Model:** [`google/embeddinggemma-2`](https://huggingface.co/google/embeddinggemma-2), 740 mln parametrów, Apache 2.0, ponad 100 języków.
- **Silnik:** Python, FastAPI, SQLite (FTS5), NumPy, PyTorch na GPU Apple (MPS).
- **Aplikacja:** Swift i WebKit.

Szczegóły znajdziesz w [specyfikacji](docs/SPECYFIKACJA.md).

## Licencja

[MIT](LICENSE) © Smart Pixel. Model EmbeddingGemma 2 © Google, licencja Apache 2.0.
