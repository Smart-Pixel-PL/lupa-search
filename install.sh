#!/bin/bash
# Lupa installer: checks prerequisites, installs Python deps, downloads the model, builds Lupa.app.
set -e
cd "$(dirname "$0")"
B=$'\e[1m'; G=$'\e[32m'; Y=$'\e[33m'; R=$'\e[31m'; N=$'\e[0m'
say()  { echo "${B}▸ $*${N}"; }
ok()   { echo "  ${G}✓${N} $*"; }
warn() { echo "  ${Y}!${N} $*"; }
die()  { echo "  ${R}✗ $*${N}"; exit 1; }
ask()  { read -r -p "  $1 [T/n] " a; [[ -z "$a" || "$a" =~ ^[TtYy] ]]; }

echo "${B}🔎 Lupa — instalacja / installation${N}"

say "System"
[[ "$(uname)" == "Darwin" ]] || die "Lupa działa tylko na macOS / macOS only"
if [[ "$(uname -m)" == "arm64" ]]; then ok "Apple Silicon"; else warn "Intel Mac: zadziała, ale indeksowanie będzie dużo wolniejsze (brak GPU MPS)"; fi

say "Xcode Command Line Tools (kompilator Swift)"
if xcrun --find swiftc >/dev/null 2>&1; then ok "swiftc"; else
  warn "Brak — uruchamiam instalację. Po jej zakończeniu uruchom ./install.sh ponownie."
  xcode-select --install || true; exit 1
fi

say "Homebrew"
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
if command -v brew >/dev/null; then ok "brew"; else
  die "Zainstaluj Homebrew: https://brew.sh i uruchom ./install.sh ponownie"
fi

for tool in uv ffmpeg; do
  say "$tool"
  if command -v "$tool" >/dev/null; then ok "$tool"; else
    if ask "Brak $tool. Zainstalować przez Homebrew?"; then brew install "$tool"; else die "$tool jest wymagany"; fi
  fi
done

say "Biblioteki Pythona (ok. 1,2 GB, chwilę potrwa)"
uv sync --frozen
ok "gotowe"

say "Model EmbeddingGemma 2 (ok. 1,4 GB, jednorazowo)"
uv run --frozen python -c "from huggingface_hub import snapshot_download; snapshot_download('google/embeddinggemma-2')" >/dev/null
ok "pobrany"

say "Konfiguracja"
[[ -f lupa.toml ]] && ok "lupa.toml już istnieje" || { cp lupa.example.toml lupa.toml; ok "utworzono lupa.toml (indeksowany folder domowy ~)"; }
mkdir -p data

say "Aplikacja Lupa.app"
./macapp/build.sh
ok "/Applications/Lupa.app"

say "Autostart"
if ask "Uruchamiać Lupę automatycznie po zalogowaniu?"; then
  osascript -e 'tell application "System Events" to if not (exists login item "Lupa") then make login item at end with properties {path:"/Applications/Lupa.app", hidden:true}' >/dev/null && ok "dodano do elementów logowania"
fi

echo
echo "${G}${B}✅ Gotowe!${N}"
echo "  1. Nadaj Lupie dostęp do plików: Ustawienia systemowe → Prywatność i ochrona → ${B}Pełny dostęp do dysku${N} → ＋ → Aplikacje → Lupa"
echo "  2. Otwórz Lupę (⌥⌘L). Pierwsze indeksowanie startuje samo i może potrwać kilka godzin."
open "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles" 2>/dev/null || true
