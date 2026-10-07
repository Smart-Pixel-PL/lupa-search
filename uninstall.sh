#!/bin/bash
# Removes Lupa.app and its login item. Optionally the index and the downloaded model.
cd "$(dirname "$0")"
read -r -p "Usunąć Lupę? [t/N] " a; [[ "$a" =~ ^[TtYy] ]] || exit 0
osascript -e 'tell application "Lupa" to quit' 2>/dev/null
osascript -e 'tell application "System Events" to if exists login item "Lupa" then delete login item "Lupa"' 2>/dev/null
rm -rf /Applications/Lupa.app && echo "✓ Usunięto /Applications/Lupa.app"
read -r -p "Usunąć też indeks i miniatury (data/)? [t/N] " a; [[ "$a" =~ ^[TtYy] ]] && rm -rf data && echo "✓ data/"
read -r -p "Usunąć pobrany model (~1,4 GB w ~/.cache/huggingface)? [t/N] " a
[[ "$a" =~ ^[TtYy] ]] && rm -rf ~/.cache/huggingface/hub/models--google--embeddinggemma-2 && echo "✓ model"
echo "Folder projektu możesz teraz usunąć ręcznie."
