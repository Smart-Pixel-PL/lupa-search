#!/bin/bash

# Required parameters:
# @raycast.schemaVersion 1
# @raycast.title Lupa — szukaj plików
# @raycast.mode silent

# Optional parameters:
# @raycast.icon 🔎
# @raycast.packageName Lupa
# @raycast.description Otwiera lokalną wyszukiwarkę plików (EmbeddingGemma 2)

# Lupa.app starts the background server itself if needed and toggles its window.
open -a "/Applications/Lupa.app"
