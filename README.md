<p align="center">
  <img src="docs/icon.png" width="128" alt="Lupa icon">
</p>

<h1 align="center">Lupa</h1>

<p align="center">
  <b>Find any file on your Mac by describing it.</b><br>
  Local, private, multimodal file search powered by Google <b>EmbeddingGemma 2</b>.
</p>

<p align="center">
  <a href="README.pl.md">🇵🇱 Po polsku</a> ·
  <a href="#install">Install</a> ·
  <a href="#features">Features</a> ·
  <a href="#how-it-works">How it works</a> ·
  <a href="docs/SPECYFIKACJA.md">Full specification (PL)</a>
</p>

<p align="center">
  <img alt="macOS" src="https://img.shields.io/badge/macOS-13%2B-black?logo=apple">
  <img alt="Apple Silicon" src="https://img.shields.io/badge/Apple%20Silicon-optimized-8e6cff">
  <img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-blue">
  <img alt="Status" src="https://img.shields.io/badge/status-beta-orange">
  <img alt="100% local" src="https://img.shields.io/badge/100%25-local%20%26%20private-2bb673">
</p>

---

Type *"photo of a football team on artificial turf"*, *"invoice for transport 2025"* or *"logo on a transparent background"*, and Lupa finds the right files even when the file names say nothing about them. Lupa understands document text, what is shown in images, video frames and audio, because it turns every file into a vector in one shared space of meanings.

**Nothing leaves your Mac.** No cloud, no account, no telemetry. The internet is needed only once, to download the model.

> **Beta.** Lupa works well day to day, but it is a young project. The user interface is currently in **Polish** (an English UI is planned). Bug reports and pull requests are welcome.

## Features

- 🔎 **Search by meaning.** Semantic and keyword search (hybrid ranking) across file names, folders, document text, images, video frames and audio.
- 🖼️ **Built for visual people.** Large thumbnail grid, fast preview panel, full-screen viewer (←/→), and videos that play on hover and jump to the matching moment.
- 🧲 **Search by image.** Drop or paste a picture to find visually similar files. **"Similar"** works on any file.
- 🏷️ **Tags and collections.** Colored tags for many files at once, favorites, and saved searches (*Invoices*, *Contracts*, *Logos*, *Screenshots*, …).
- 🎛️ **Filters and sorting.** By type, date, drive, folder shortcuts (Downloads, Desktop, Documents, iCloud Drive) and tags. Sort by relevance, date, name, size or type.
- 🧹 **Porządki: disk cleanup for your Mac.** Finds reclaimable space on the system disk (app caches, logs, old installers, leftovers of uninstalled apps, byte-identical duplicates, unused apps, big old files) and moves what you select to the Trash. More below.
- 🗄️ **NAS-friendly.** Network drives are supported. The index and thumbnails stay local, so you can browse even when the NAS is offline, and Lupa re-indexes automatically when it comes back.
- 🍏 **Native Mac app.** Dock icon, global hotkey **⌥⌘L**, starts at login, light and dark mode.
- ⌨️ **Keyboard-first.** `/` search · arrows navigate · `Space` preview · `Q` full screen · `Enter` open · `⌘Enter` reveal in Finder · `S` similar · `T` tag · `F` favorite.

## Requirements

- macOS 13 or later. **Apple Silicon** recommended (Intel works, but indexing is much slower).
- About **3 GB** of free disk space (model 1.4 GB + Python libraries 1.2 GB), plus the index.
- [Homebrew](https://brew.sh) and Xcode Command Line Tools. The installer takes care of `uv` and `ffmpeg`.
- 16 GB RAM recommended.

## Install

```bash
git clone https://github.com/Smart-Pixel-PL/lupa-search.git
cd lupa-search
./install.sh
```

Or download the ZIP from the green **Code** button, unzip it and run `./install.sh` in that folder.

The installer:
1. checks the prerequisites and installs `uv` and `ffmpeg` if they are missing;
2. installs the Python dependencies into a local `.venv`;
3. downloads **EmbeddingGemma 2** (one time, about 1.4 GB);
4. builds **Lupa.app** and puts it in `/Applications`;
5. optionally adds Lupa to your login items.

**Then grant access to your files.** Open *System Settings → Privacy & Security → **Full Disk Access***, click ＋ and add **Lupa**, then quit Lupa (⌘Q) and open it again. Without this, macOS silently hides Downloads, Desktop and Documents from Lupa.

> Keep the project folder after installing: **Lupa.app** runs the search engine from it.

### Uninstall

```bash
./uninstall.sh
```

## Usage

Open **Lupa** from Launchpad, or press **⌥⌘L** anywhere. The first indexing pass starts automatically:

1. **File scan:** seconds for a local disk, longer for a NAS.
2. **Names and folders:** everything becomes searchable by name and location.
3. **Thumbnails:** the grid fills with real previews.
4. **Content analysis:** images, PDFs, Office documents, video and audio. On a large library this can take hours. It runs at low priority, and you can pause it in the bottom-left corner.

After that, Lupa re-scans every 3 hours and only analyses new or changed files.

### Configuration

Edit `lupa.toml` in the project folder (it is created from [`lupa.example.toml`](lupa.example.toml) on first run), then restart Lupa.

| Key | Meaning | Default |
|---|---|---|
| `roots` | folders to index (add e.g. `"/Volumes/NAS"`) | `["~"]` |
| `places` | folder shortcuts in the sidebar | Downloads, Desktop, Documents, Pictures, iCloud Drive |
| `image_tokens` | image detail: `70` fastest · `140` balanced · `280` full quality | `140` |
| `reindex_every_hours` | automatic re-scan interval | `3` |
| `exclude_dirs` / `exclude_paths` | what to skip | `node_modules`, `.git`, caches, … |
| `[cleanup]` | Porządki: scan interval, low-space alert, age and size thresholds | 7 days, 15%, … |

To use a different hotkey, assign one to the **Lupa** application in Raycast or Alfred.

## Porządki (disk cleanup)

A second tab that keeps your **local disk** clean, in layers from most to least certain:

1. **Hard guards.** macOS system locations, Keychains, Mail, Messages, iCloud Drive and Lupa itself are never touched. Only `/Users/…` and `/Applications/…` are in scope, and network drives are excluded.
2. **Safe by definition.** App and developer caches (npm, pip, uv, Gradle, Xcode), logs and `.dmg`/`.pkg` installers. Apps rebuild these themselves.
3. **Leftovers of uninstalled apps.** `~/Library` folders are matched against the bundle IDs and names of every installed app, and must be untouched for at least 30 days.
4. **Real usage.** macOS "last opened" dates for apps, Downloads and big files.

| Category | Risk | Preselected |
|---|---|---|
| App caches · developer caches · logs | safe | yes (except apps that are currently running) |
| Installers (.dmg, .pkg, .iso) | safe | if older than 30 days |
| Leftovers of uninstalled apps · old Downloads · unused apps | check | no |
| Duplicates (byte-identical, BLAKE2 checksum) | check | only obvious copies; the best copy always stays |
| Big, long-unused files · iPhone/iPad backups | careful | no |

**Apps are uninstalled properly, not just trashed.** Normal and App Store apps go to the Trash together with their own `~/Library` data, matched by exact bundle ID. Adobe apps send you to Creative Cloud, and apps with their own uninstaller or with system components (privileged helpers, system extensions, launch daemons) send you to the vendor's uninstaller. Running apps, login items and background services are never listed as "unused".

**Nothing is deleted by Lupa.** Selected items go to the **Trash via Finder**, so *Put Back* works. Only paths from the latest scan can be trashed, and every action is logged. A scan takes about 15–20 s. Lupa re-scans weekly, sends a macOS notification when free space drops below 15%, and can optionally move caches and logs to the Trash every week. On the author's MacBook (460 GB, 7% free), the first scan found about 52 GB to reclaim.

## How it works

```
Lupa.app (Swift + WebKit)  ──►  local server (FastAPI, 127.0.0.1:7766)  ──►  SQLite + vectors in memory
                                      ▲
                                indexer (EmbeddingGemma 2 on the Apple GPU via PyTorch MPS)
```

| | |
|---|---|
| **Model** | [`google/embeddinggemma-2`](https://huggingface.co/google/embeddinggemma-2): 740M parameters (text 270M + vision 170M + audio 300M), Apache 2.0, 100+ languages, 8K context |
| **Vectors** | 768 → **256 dimensions** (Matryoshka), stored as float16 |
| **Images** | encoded on the GPU at 140 tokens per image (about 3.7 images/s on M2 Pro) |
| **Video** | 4 frames per video, so results can jump to the matching frame |
| **Audio** | a 30-second clip at 16 kHz |
| **Documents** | PDF (PyMuPDF; scanned pages are understood visually), DOCX/PPTX/XLSX, DOC/RTF (macOS `textutil`), text and code |
| **Design files** | PSD, AI, SVG, RAW, Sketch, Pages/Keynote… rendered through macOS Quick Look |
| **Queries** | encoded on the **CPU** (about 60 ms), so search stays fast while the GPU is busy indexing |
| **Ranking** | scores are z-normalized per vector type (name / text / image / audio), plus an SQLite FTS5 keyword boost |

Details (architecture, file types, performance, security) are in the [specification](docs/SPECYFIKACJA.md) (Polish).

## Privacy and security

- The server listens on `127.0.0.1` only and checks the `Host` header, which protects against DNS rebinding.
- Actions that change state need a custom header and a same-origin request, so websites you visit cannot trigger them.
- The model loads offline (`local_files_only`). Lupa has no analytics and makes no network calls after installation.

## Known limitations

- The user interface is in Polish only, for now.
- The first full analysis of a large library takes hours. Network drives over SMB are slower.
- Lupa.app is ad-hoc signed (not notarized). After a rebuild, macOS may ask you to grant Full Disk Access again.
- No OCR yet. Scanned PDFs are matched visually.

## Roadmap

- English UI and language switch
- OCR for scans (Tesseract)
- Duplicate detection
- Porządki: local Gemma 4 adviser that explains unknown folders
- Signed and notarized release builds

## License

[MIT](LICENSE) © Smart Pixel.
The EmbeddingGemma 2 model is © Google and licensed under Apache 2.0.

<sub>Built with ❤️ in Poland, with the help of Claude Code.</sub>
