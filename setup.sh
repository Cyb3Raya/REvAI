#!/usr/bin/env bash
set -euo pipefail
# ============================================================
# Determine script location (works from any cwd)
# ============================================================
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$SCRIPT_DIR"
GHIDRA_DIR="$REPO_ROOT/Ghidra"
SCRIPTS_DIR="$REPO_ROOT/Scripts"
VENV_DIR="$REPO_ROOT/revai-venv"
REPO_OWNER="NationalSecurityAgency"
REPO_NAME="ghidra"
GITHUB_API_URL="https://api.github.com/repos/${REPO_OWNER}/${REPO_NAME}/releases/latest"
# ============================================================
# Helpers
# ============================================================
need_cmd() { command -v "$1" >/dev/null 2>&1; }
apt_update_once() {
  if [[ -z "${_APT_UPDATED:-}" ]]; then
    _APT_UPDATED=1
    sudo apt-get update -y
  fi
}
apt_install_if_missing() {
  local pkg="$1"
  if dpkg -s "$pkg" >/dev/null 2>&1; then
    return 0
  fi
  if ! need_cmd sudo; then
    echo "ERROR: 'sudo' not found and package '$pkg' is missing. Install it manually."
    exit 1
  fi
  echo "Installing missing package: $pkg"
  apt_update_once
  sudo apt-get install -y "$pkg"
}
# Best-effort install for packages that may not exist on all distros/versions
apt_install_try() {
  local pkg="$1"
  if dpkg -s "$pkg" >/dev/null 2>&1; then
    return 0
  fi
  if ! need_cmd sudo; then
    return 0
  fi
  echo "Attempting to install optional package: $pkg"
  apt_update_once
  if ! sudo apt-get install -y "$pkg"; then
    echo "Optional package not available (ok): $pkg"
  fi
}
# Pick best python3.X available (highest minor)
detect_best_python() {
  local p
  p="$(ls -1 /usr/bin/python3.* 2>/dev/null | grep -E '/usr/bin/python3\.[0-9]+$' | sort -V | tail -n 1 || true)"
  if [[ -n "${p:-}" && -x "$p" ]]; then
    echo "$p"
  else
    command -v python3
  fi
}
install_pyghidra_noninteractive() {
  local ghidra_install_dir="$1"
  local cfg_dir="$HOME/.config/ghidra/$(basename "$ghidra_install_dir")"
  local venv_dir="$cfg_dir/venv"
  local dist_dir="$ghidra_install_dir/Ghidra/Features/PyGhidra/pypkg/dist"
  if [[ ! -d "$dist_dir" ]]; then
    echo "ERROR: PyGhidra wheel directory not found:"
    echo " $dist_dir"
    exit 1
  fi
  mkdir -p "$cfg_dir"
  # If an old/broken venv exists, remove it to avoid "No module named pip"
  if [[ -d "$venv_dir" ]]; then
    echo "Removing existing PyGhidra venv so it can be recreated cleanly:"
    echo " $venv_dir"
    rm -rf "$venv_dir"
  fi
  local pybin
  pybin="$(detect_best_python)"
  echo "Using Python for PyGhidra venv: $pybin"
  # Try to install matching python3.X-venv package if applicable (best-effort)
  if [[ "$pybin" =~ /usr/bin/python3\.[0-9]+$ ]]; then
    local py_minor
    py_minor="$(basename "$pybin")" # python3.13
    apt_install_try "${py_minor}-venv" # python3.13-venv (optional)
  fi
  echo "Creating PyGhidra venv at: $venv_dir"
  "$pybin" -m venv "$venv_dir"
  # Activate and install from local wheelhouse
  (
    source "$venv_dir/bin/activate"
    python -m ensurepip --upgrade >/dev/null 2>&1 || true
    python -m pip install --upgrade pip setuptools wheel
    echo "Installing PyGhidra from Ghidra bundled wheels (no internet)..."
    python -m pip install --no-index --find-links "$dist_dir" pyghidra
    echo "Verifying PyGhidra import..."
    python - <<'PY'
import pyghidra
import jpype
print("OK: pyghidra version:", pyghidra.__version__)
print("OK: jpype version:", jpype.__version__)
PY
  )
  echo "PyGhidra installed and verified in:"
  echo " $venv_dir"
}
# ============================================================
# Dependency install for Ubuntu/Mint
# ============================================================
echo "Checking and installing required dependencies for Ubuntu/Mint..."
apt_install_if_missing "curl"
apt_install_if_missing "jq"
apt_install_if_missing "wget"
apt_install_if_missing "unzip"
apt_install_if_missing "python3"
apt_install_if_missing "python3-pip"
apt_install_if_missing "python3-venv"
apt_install_if_missing "openjdk-21-jdk"  # For Ghidra 12.0+; JDK (not JRE) required
# Sanity checks
for cmd in curl jq wget unzip python3; do
  if ! need_cmd "$cmd"; then
    echo "ERROR: Required command not found after install attempt: $cmd"
    exit 1
  fi
done
if ! python3 -c 'import venv' >/dev/null 2>&1; then
  echo "ERROR: python3-venv is not functional. Try: sudo apt-get install -y python3-venv"
  exit 1
fi
if ! need_cmd java; then
  echo "ERROR: Java not found. Ghidra requires Java 21+ JDK."
  exit 1
fi
# Verify we have JDK (not just JRE) by checking for javac
if ! need_cmd javac; then
  echo "ERROR: JDK not found (javac missing). Ghidra requires the full JDK, not just JRE."
  echo "Install with: sudo apt-get install -y openjdk-21-jdk"
  exit 1
fi
JAVA_VERSION=$(java -version 2>&1 | head -n 1 | awk -F '"' '{print $2}' | cut -d'.' -f1)
if [[ "$JAVA_VERSION" -lt 17 ]]; then
  echo "ERROR: Java $JAVA_VERSION detected. Ghidra 12.0+ requires Java 17 or newer."
  exit 1
fi
echo "Java JDK $JAVA_VERSION detected - OK"
echo "Dependencies look good."
# ============================================================
# Step 1: Create required directories
# ============================================================
echo "Creating directories (if they don't exist)..."
mkdir -p "$GHIDRA_DIR"
mkdir -p "$GHIDRA_DIR/Decompiled_JSON"
mkdir -p "$GHIDRA_DIR/Projects"
mkdir -p "$SCRIPTS_DIR"
# ============================================================
# Step 2: Download and install latest Ghidra into ./Ghidra
# ============================================================
cd "$GHIDRA_DIR"
echo "Working in Ghidra directory: $PWD"
echo "Fetching latest Ghidra release information..."
# Fixed: Handle curl with optional GitHub token properly
if [[ -n "${GITHUB_TOKEN:-}" ]]; then
  API_JSON="$(curl -s -H "Authorization: Bearer ${GITHUB_TOKEN}" "$GITHUB_API_URL")"
else
  API_JSON="$(curl -s "$GITHUB_API_URL")"
fi
LATEST_URL="$(echo "$API_JSON" | jq -r '
  .assets[]
  | select(.name | test("^ghidra_.*_PUBLIC_.*\\.zip$"; "i"))
  | .browser_download_url
' | head -n 1)"
if [[ -z "$LATEST_URL" || "$LATEST_URL" == "null" ]]; then
  echo "ERROR: Could not find Ghidra PUBLIC zip in latest release"
  echo "Available assets:"
  echo "$API_JSON" | jq -r '.assets[].name'
  exit 1
fi
ZIP_NAME="$(basename "$LATEST_URL")"
echo "Latest Ghidra zip: $ZIP_NAME"
if [[ -f "$ZIP_NAME" ]]; then
  echo "Zip already exists, skipping download"
else
  echo "Downloading $ZIP_NAME..."
  wget -O "$ZIP_NAME" "$LATEST_URL"
fi
echo "Unzipping $ZIP_NAME quietly (this may take a few minutes)..."
unzip -qq -o "$ZIP_NAME"
EXTRACTED_DIR="$(ls -1d ghidra_*_PUBLIC 2>/dev/null | sort -V | tail -n1 || true)"
if [[ -z "${EXTRACTED_DIR:-}" ]]; then
  echo "ERROR: Could not detect Ghidra directory after extraction"
  exit 1
fi
GHIDRA_INSTALL_DIR="$GHIDRA_DIR/$EXTRACTED_DIR"
echo "Ghidra extracted/updated to: $GHIDRA_INSTALL_DIR"
# ============================================================
# Step 2.5: Copy DecompileFunctions.py to Ghidra scripts
# ============================================================
GHIDRA_SCRIPTS_DIR="$GHIDRA_INSTALL_DIR/Ghidra/Features/Base/ghidra_scripts"
if [[ -f "$SCRIPTS_DIR/DecompileFunctions.py" ]]; then
  echo "Copying DecompileFunctions.py to Ghidra scripts directory..."
  cp "$SCRIPTS_DIR/DecompileFunctions.py" "$GHIDRA_SCRIPTS_DIR/"
  echo "Script installed successfully"
else
  echo "WARNING: $SCRIPTS_DIR/DecompileFunctions.py not found!"
  echo "Script will need to be added manually"
fi
# ============================================================
# Step 3: Setup RevAI virtual environment in repo root (FRESH EACH RUN)
# ============================================================
cd "$REPO_ROOT"
echo "Setting up RevAI virtual environment in $REPO_ROOT..."
if [[ -d "$VENV_DIR" ]]; then
  echo "Removing existing repo venv (fresh venv each run): $VENV_DIR"
  rm -rf "$VENV_DIR"
fi
echo "Creating new virtual environment: $(basename "$VENV_DIR")"
python3 -m venv "$VENV_DIR"
echo "Installing/upgrading Python packages in the venv..."
(
  source "$VENV_DIR/bin/activate"
  python3 -m pip install --upgrade pip
  # anthropic: Claude models | openai: OpenAI + any OpenAI-compatible server
  # (LM Studio, vLLM, llama.cpp server) | Ollama is reached over its REST API (requests)
  python3 -m pip install --upgrade streamlit openai anthropic requests
)
# ============================================================
# Step 3.5: Check for Ollama (optional, for local open-source models)
# ============================================================
if need_cmd ollama; then
  echo "Ollama detected: $(ollama --version 2>/dev/null | head -n 1)"
  if ! curl -s --max-time 2 http://localhost:11434/api/tags >/dev/null 2>&1; then
    echo "NOTE: Ollama is installed but not running. Start it with: ollama serve"
  fi
else
  echo "NOTE: Ollama not found. Local models are optional."
  echo "      Install from https://ollama.com/download, then pull a model, e.g.:"
  echo "      ollama pull qwen2.5-coder"
fi
# ============================================================
# Step 4: Create helper script for headless decompilation
# ============================================================
echo "Creating headless decompilation helper script..."
cat > "$REPO_ROOT/decompile_binary.sh" << 'HEREDOC'
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
HEREDOC
chmod +x "$REPO_ROOT/decompile_binary.sh"
echo "Created: decompile_binary.sh"
# ============================================================
# Step 4.5: Create PyGhidra GUI launcher script
# ============================================================
echo "Creating PyGhidra GUI launcher script..."
cat > "$REPO_ROOT/launch_pyghidra.sh" << 'HEREDOC'
#!/usr/bin/env bash
set -e

# Quick launcher for PyGhidra GUI
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GHIDRA_DIR="$SCRIPT_DIR/Ghidra"
GHIDRA_INSTALL_DIR="$(ls -1d "$GHIDRA_DIR"/ghidra_*_PUBLIC 2>/dev/null | sort -V | tail -n1)"

if [[ -z "$GHIDRA_INSTALL_DIR" ]]; then
    echo "ERROR: Ghidra installation not found"
    exit 1
fi

PYGHIDRA="$GHIDRA_INSTALL_DIR/support/pyghidraRun"

if [[ ! -f "$PYGHIDRA" ]]; then
    echo "WARNING: pyghidraRun not found, launching standard Ghidra GUI instead..."
    cd "$GHIDRA_INSTALL_DIR"
    ./ghidraRun
else
    echo "Launching PyGhidra GUI..."
    cd "$GHIDRA_INSTALL_DIR/support"
    ./pyghidraRun
fi
HEREDOC
chmod +x "$REPO_ROOT/launch_pyghidra.sh"
echo "Created: launch_pyghidra.sh"
# ============================================================
# Step 5: Install + verify PyGhidra (non-interactive, reliable)
# ============================================================
echo ""
read -p "Do you want to install and verify PyGhidra now (recommended for Python 3 scripting support)? (y/n) " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
  install_pyghidra_noninteractive "$GHIDRA_INSTALL_DIR"
fi
# ============================================================
# Step 6: Launch PyGhidra GUI (optional)
# ============================================================
echo ""
read -p "Do you want to launch the Ghidra GUI with PyGhidra support now? (y/n) " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
  PYGHIDRA_SCRIPT="$GHIDRA_INSTALL_DIR/support/pyghidraRun"
  if [[ -f "$PYGHIDRA_SCRIPT" ]]; then
    echo "Launching Ghidra GUI with PyGhidra support..."
    ( cd "$GHIDRA_INSTALL_DIR/support" && ./pyghidraRun )
  else
    echo "WARNING: pyghidraRun not found at $PYGHIDRA_SCRIPT"
    echo "Launching standard Ghidra GUI instead..."
    ( cd "$GHIDRA_INSTALL_DIR" && ./ghidraRun )
  fi
fi
# ============================================================
# Step 7: Launch REvAI GUI (optional)
# ============================================================
if [[ -f "$SCRIPTS_DIR/REvAI_GUI.py" ]]; then
  echo ""
  read -p "Do you want to launch the REvAI GUI now? (y/n) " -n 1 -r
  echo
  if [[ $REPLY =~ ^[Yy]$ ]]; then
    echo "Launching REvAI GUI..."
    (
      source "$VENV_DIR/bin/activate"
      streamlit run "$SCRIPTS_DIR/REvAI_GUI.py"
    )
  fi
else
  echo "WARNING: $SCRIPTS_DIR/REvAI_GUI.py not found!"
  echo "Make sure Scripts/REvAI_GUI.py exists."
fi
# ============================================================
# Done
# ============================================================
echo
echo "=========================================="
echo "Setup complete!"
echo "=========================================="
echo "Repository root : $REPO_ROOT"
echo "Ghidra directory : $GHIDRA_DIR"
echo "Ghidra install dir : $GHIDRA_INSTALL_DIR"
echo "Scripts directory : $SCRIPTS_DIR"
echo "Repo venv : $VENV_DIR"
echo ""
echo "QUICK START:"
echo " ./launch_pyghidra.sh              # Launch Ghidra GUI with PyGhidra support"
echo " ./decompile_binary.sh <binary>    # Decompile a binary to JSON"
echo " source revai-venv/bin/activate"
echo " streamlit run Scripts/REvAI_GUI.py"
echo ""
echo "MANUAL LAUNCH OPTIONS:"
echo " cd $GHIDRA_INSTALL_DIR/support && ./pyghidraRun  # PyGhidra GUI"
echo " cd $GHIDRA_INSTALL_DIR && ./ghidraRun            # Standard GUI"
echo "=========================================="