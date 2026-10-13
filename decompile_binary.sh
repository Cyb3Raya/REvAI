#!/usr/bin/env bash
set -euo pipefail
# Usage: ./decompile_binary.sh <path_to_binary> [output_json_path]
# Runs Ghidra headless under PyGhidra (required for Python scripts on Ghidra 12.1+)
if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <path_to_binary> [output_json_path]"
  exit 1
fi
BINARY_PATH="$(realpath "$1")"
OUTPUT_JSON="${2:-}"
if [[ ! -f "$BINARY_PATH" ]]; then
  echo "ERROR: Binary file not found: $1"
  exit 1
fi
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GHIDRA_DIR="$SCRIPT_DIR/Ghidra"
REPO_SCRIPTS="$SCRIPT_DIR/Scripts"
PROJECTS_DIR="$GHIDRA_DIR/Projects"
DECOMPILED_DIR="$GHIDRA_DIR/Decompiled_JSON"
GHIDRA_INSTALL_DIR="$(ls -1d "$GHIDRA_DIR"/ghidra_*_PUBLIC 2>/dev/null | sort -V | tail -n1 || true)"
if [[ -z "${GHIDRA_INSTALL_DIR:-}" ]]; then
  echo "ERROR: Ghidra installation not found in $GHIDRA_DIR"
  echo "Run setup.sh first"
  exit 1
fi
PYGHIDRA_RUN="$GHIDRA_INSTALL_DIR/support/pyghidraRun"
if [[ ! -x "$PYGHIDRA_RUN" ]]; then
  echo "ERROR: pyghidraRun not found at $PYGHIDRA_RUN"
  exit 1
fi
if [[ ! -f "$REPO_SCRIPTS/DecompileFunctions.py" ]]; then
  echo "ERROR: $REPO_SCRIPTS/DecompileFunctions.py not found"
  exit 1
fi
BINARY_NAME="$(basename "$BINARY_PATH")"
PROJECT_NAME="revai_${BINARY_NAME%.*}_$(date +%s)"
if [[ -z "$OUTPUT_JSON" ]]; then
  OUTPUT_JSON="$DECOMPILED_DIR/${BINARY_NAME%.*}_decompiled.json"
fi
mkdir -p "$PROJECTS_DIR" "$DECOMPILED_DIR" "$(dirname "$OUTPUT_JSON")"
OUTPUT_JSON="$(realpath -m "$OUTPUT_JSON")"
rm -f "$OUTPUT_JSON"  # so a stale file can't hide a failed run
echo "Binary: $BINARY_PATH"
echo "Project: $PROJECT_NAME"
echo "Output JSON: $OUTPUT_JSON"
echo ""
# If the REvAI venv is active, step out of it so pyghidraRun uses Ghidra's own PyGhidra venv
# (set up by setup.sh) instead of trying to install PyGhidra into revai-venv
if [[ -n "${VIRTUAL_ENV:-}" ]]; then
  PATH="$(echo "$PATH" | tr ':' '\n' | grep -vxF "$VIRTUAL_ENV/bin" | paste -sd: -)"
  export PATH
  unset VIRTUAL_ENV
fi
# Scripts are loaded straight from the repo's Scripts/ folder, so edits there take effect immediately
"$PYGHIDRA_RUN" --headless \
  "$PROJECTS_DIR" \
  "$PROJECT_NAME" \
  -import "$BINARY_PATH" \
  -scriptPath "$REPO_SCRIPTS" \
  -postScript DecompileFunctions.py "$OUTPUT_JSON" \
  -deleteProject
echo ""
if [[ -s "$OUTPUT_JSON" ]]; then
  echo "Done. Output saved to: $OUTPUT_JSON"
  echo "Next:"
  echo " source revai-venv/bin/activate"
  echo " streamlit run Scripts/REvAI_GUI.py"
else
  echo "ERROR: No JSON was produced. Look for 'SCRIPT ERROR' in the output above."
  exit 1
fi
