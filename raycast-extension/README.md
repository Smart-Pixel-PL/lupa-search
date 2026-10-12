# Lupa for Raycast

Search all your files (Mac + NAS) by meaning, straight from Raycast — using the local [Lupa](../README.md) server
(EmbeddingGemma 2, nothing leaves your Mac).

- **Szukaj w Lupie**: thumbnail grid, filter by type, ↵ open, ⌘Y Quick Look, ⌘S similar files, ⌘⇧C copy the file
- **Otwórz Lupę**: shows the Lupa window

Requires Lupa.app to be running (it serves `http://127.0.0.1:7766`).

## Install

```bash
cd raycast-extension
npm install
npm run dev     # imports the extension into Raycast; you can stop it afterwards (Ctrl+C), the extension stays
```
