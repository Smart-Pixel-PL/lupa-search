#!/bin/bash
# Starts the Lupa server (Lupa.app runs this as its background engine).
cd "$(dirname "$0")/.."
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
export OPENBLAS_NUM_THREADS=1  # avoids a fork() deadlock in OpenBLAS when spawning du/ffmpeg/osascript
exec uv run --frozen python -m lupa.server
