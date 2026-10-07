#!/bin/bash
# Starts the Lupa server (Lupa.app runs this as its background engine).
cd "$(dirname "$0")/.."
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
exec uv run --frozen python -m lupa.server
