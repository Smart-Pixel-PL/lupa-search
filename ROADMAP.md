# Lupa — lista funkcji do wdrożenia

Wykreślamy po kolei: `- [x] ~~funkcja~~` = zrobione (z datą i wersją).

> Uwagi do v0.4.0: wizualne grupowanie łączy tylko kopie praktycznie identyczne (próg 0,99) — pomiar na 1 894 projektach
> sublimacji pokazał, że różne wzory na tym samym szablonie są dla AI prawie tak samo podobne jak warianty jednego wzoru,
> więc wersje rozpoznajemy przede wszystkim po nazwie. Przeciąganie plików działa w Lupa.app (w Chrome — pobieranie).

## 1. Przypięte foldery („Gdzie”)
- [x] ~~Przypinanie folderu: przycisk „📌 Przypnij folder” w podglądzie pliku i „＋” w sekcji Gdzie (systemowe okno wyboru folderu)~~ — v0.4.0 (12.10.2026)
- [x] ~~Kliknięcie przypiętego folderu zawęża wyszukiwanie do niego i jego podfolderów; działa ze wszystkimi filtrami~~ — v0.4.0 (12.10.2026)
- [x] ~~Kilka folderów naraz (⌘-klik)~~ — v0.4.0 (12.10.2026)
- [x] ~~„Ostatnio przeglądane” foldery podpowiadane automatycznie~~ — v0.4.0 (12.10.2026)
- [x] ~~Własna nazwa i ikona przypiętego folderu (prawy klik)~~ — v0.4.0 (12.10.2026)

## 2. Praca z plikami
- [x] ~~Przeciąganie plików z Lupy do innych aplikacji (mail, Slack, Affinity, Finder)~~ — v0.4.0 (12.10.2026)
- [x] ~~„Zbierz zaznaczone”: ZIP z zaznaczonych plików~~ — v0.4.0 (12.10.2026)
- [x] ~~„Zbierz zaznaczone”: kopiuj zaznaczone do wybranego folderu~~ — v0.4.0 (12.10.2026)

## 3. Wersje i prawie-duplikaty
- [x] ~~Grupowanie wersji tego samego projektu w wynikach (np. `projekt_66_01`, `_02`, `_final`) — jedna karta „+N wersji”~~ — v0.4.0 (12.10.2026)
- [x] ~~Grupowanie wizualnie prawie identycznych obrazów (podobieństwo Gemma)~~ — v0.4.0 (12.10.2026)
- [x] ~~Pasek „Wersje” w podglądzie — szybkie przełączanie między wariantami~~ — v0.4.0 (12.10.2026)

## 4. Pod pracę graficzną
- [x] ~~Przeglądarka folderów z miniaturami (nawigacja jak w Finderze)~~ — v0.5.0 (12.10.2026)
- [x] ~~Wyszukiwanie po kolorze (paleta → pliki w danym kolorze)~~ — v0.5.0 (12.10.2026)

## 5. AI (lokalnie)
- [x] ~~Pytania do dokumentów (Gemma 4 / Ollama) z odnośnikami do plików~~ — v0.6.0 — zakładka „✨ Zapytaj” (domyślnie gemma3:4b, Gemma 4 do włączenia: `ask_model`) (12.10.2026)
- [x] ~~OCR skanów (tekst ze zdjęć i skanów do wyszukiwania)~~ — v0.6.0 — Apple Vision (pl), skany PDF + obrazy-dokumenty (12.10.2026)
- [x] ~~Transkrypcja nagrań audio i wideo (lokalny Whisper)~~ — v0.6.0 — Whisper large-v3-turbo (MLX), uruchamiana po zakończeniu indeksowania (12.10.2026)
- [x] ~~Podpowiedzi tagów~~ — v0.6.0 — uczone z Twoich tagów + „Znajdź pliki pasujące do tagu” (12.10.2026)

## 5b. Grafika
- [x] ~~Usuwanie tła z obrazów (logo od klientów) — kopia „<nazwa> bez tła.png” obok oryginału~~ — v0.6.1 (12.10.2026)

## 6. Wygoda i porządek
- [x] ~~Mini okno wyszukiwania (⌥⌘L jak Spotlight)~~ — v0.7.0: pływający panel, ↵ otwórz, ⌘↵ Finder, ⌘Y podgląd, ⌘O pełna Lupa, przeciąganie plików (12.10.2026)
- [x] ~~Rozszerzenie Raycast~~ — v0.7.0: `raycast-extension/`, siatka miniatur, filtr typu, Quick Look, podobne pliki, kopiowanie pliku (12.10.2026)
- [x] ~~Natychmiastowe indeksowanie nowych plików na Macu~~ — v0.6.2: FSEvents, nowy/zmieniony/usunięty plik w wyszukiwarce po ~4 s (12.10.2026)
- [x] ~~Mapa zajętości dysku~~ — v0.7.0: Porządki → 🗺 Mapa dysku, mapa kafelkowa Maca (na żywo) i NAS-a (z indeksu), wchodzenie w foldery (12.10.2026)
- [x] ~~Porządki Mac ↔ NAS (pliki zdublowane między Makiem a NAS-em)~~ — v0.7.0: kategoria „Już są na NAS-ie”, nazwa + rozmiar + suma kontrolna, przed usunięciem ponowne sprawdzenie kopii (12.10.2026)
