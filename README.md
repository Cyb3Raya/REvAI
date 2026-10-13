# REvAI

**AI-assisted reverse engineering with Ghidra.**

REvAI decompiles a binary with Ghidra in headless mode, exports every function's pseudo-C to JSON, and then uses an AI model to summarize what each function does, suggest meaningful names, and optionally reconstruct cleaner C code. It works with cloud models (Anthropic Claude, OpenAI) or fully local open-source models (Ollama, LM Studio, vLLM, llama.cpp).

```
binary ──► Ghidra (headless) ──► functions.json ──► REvAI web app ──► summaries / rename map / clean C
```

---

## Features

- **One-command decompilation:** imports, auto-analyzes, and exports all functions to JSON headlessly
- **Multiple AI providers:** Anthropic (Claude), OpenAI, Ollama, or any OpenAI-compatible local server
- **Function summaries:** one-paragraph purpose, or a step-by-step walkthrough of the logic
- **Auto-rename suggestions:** snake_case names exported as a rename map
- **Code reconstruction:** optional "clean room" C rebuilt from the summary
- **Token and cost tracking** per run (local models show $0)
- **Keys stay local:** API keys live only in the browser session or environment variables, never on disk

## Requirements

- Ubuntu or Linux Mint (setup uses `apt`)
- `sudo` access
- Internet access for the first run (downloads Ghidra and Java 21)
- Optional: [Ollama](https://ollama.com/download) for local models (8 GB+ RAM recommended)

## Setup

```bash
git clone git@github.com:cyb3raya/REvAI.git
cd REvAI
chmod +x setup.sh decompile_binary.sh
./setup.sh
```

During setup:

| Prompt | Answer |
|---|---|
| Install and verify PyGhidra? | **y** (required on Ghidra 12.1+) |
| Launch the Ghidra GUI now? | n |
| Launch the REvAI GUI now? | n |

`setup.sh` installs Java 21, downloads the latest Ghidra, sets up PyGhidra, and creates the `revai-venv` Python environment.

> **Note:** `setup.sh` rebuilds `revai-venv` from scratch every time it runs. You only need to rerun it if Ghidra is missing or broken.

## Usage

### 1. Decompile a binary

```bash
./decompile_binary.sh path/to/binary
```

This creates a temporary Ghidra project, runs auto-analysis, exports every function, and deletes the project. Output:

```
Exported 212 functions (88 thunks skipped, 3 failed)
Done. Output saved to: Ghidra/Decompiled_JSON/binary_decompiled.json
```

Thunks (jump stubs to imported functions) are skipped to save tokens.

### 2. Start the web app

```bash
source revai-venv/bin/activate
streamlit run Scripts/REvAI_GUI.py
```

Opens at `http://localhost:8501`.

### 3. Analyze

1. Upload the JSON from step 1
2. Pick a **Model Source** in the sidebar and enter your API key (or server URL for local models)
3. Click **Refresh model list** and choose a model
4. Select the functions to analyze (defaults to `main`)
5. Click **Generate Summaries**
6. Download the summaries, rename map, reconstructed C, or everything as a ZIP

### Optional: manual analysis in Ghidra

```bash
./launch_pyghidra.sh
```

`DecompileFunctions.py` is available in the Script Manager under the **REvAI** category.

## Model providers

| Provider | What you need | Notes |
|---|---|---|
| **Anthropic (Claude)** | API key, or `ANTHROPIC_API_KEY` | Uses an **Effort** setting (low to max) |
| **OpenAI** | API key, or `OPENAI_API_KEY` | GPT-5 models use a **Reasoning effort** setting |
| **Ollama** | Ollama running locally | e.g. `ollama pull qwen2.5-coder` |
| **OpenAI-compatible** | Server URL | LM Studio `:1234/v1`, vLLM `:8000/v1`, llama.cpp `:8080/v1` |

Optional environment variables: `OLLAMA_BASE_URL`, `OPENAI_COMPAT_BASE_URL`, `OPENAI_COMPAT_API_KEY`, `REVAI_OUTPUT_DIR`.

## Understanding the options

### Effort / reasoning effort

Controls how hard the model thinks about each function. It doesn't change what the model knows.

- **Low:** quick, shallow analysis. Good for bulk triage and naming.
- **Medium:** balanced. A good default.
- **High and above:** slow, deep, multi-step reasoning for complex logic.

When reasoning is enabled, temperature is turned off. Reasoning models plan in multiple steps, and adding randomness on top makes results less consistent, so the app hides the temperature slider in that mode.

### Include step-by-step explanation

Switches the prompt from "summarize this function" to "walk through the logic step by step, then summarize." You get *how*, not just *what*.

**Use it for** loaders, decryption routines, validation checks, state machines, or anything with loops and conditionals.
**Skip it for** bulk processing or quick naming passes. It increases tokens, runtime, and output size.

### Attempt code regeneration from summary

Asks the model to rebuild an approximate C function from its own summary. This is intentional: going through the summary strips compiler artifacts, decompiler noise, and junk variable names, so you get *semantic* C instead of literal C.

**Good for** understanding logic flow, documentation, teaching, and comparing behavior across samples.
**Not for** recompiling, exact opcode matching, memory layout, or writing detection signatures. Signatures should come from real strings and byte patterns in the sample, not from AI-reconstructed code.

This is the most expensive option. Renaming and regeneration run at low effort to keep costs down.

## Safety

- **Analyze malware only in an isolated VM** with no access to your host or network shares. REvAI never executes the binary, but the samples themselves are still dangerous.
- **Cloud providers receive the decompiled code.** For sensitive or proprietary samples, use a local model (Ollama, LM Studio, etc.) so nothing leaves your machine.
- **Never commit samples or binaries.** The `.gitignore` excludes common binary types, `crackmes/`, and `samples/`.

## Project layout

```
REvAI/
├── setup.sh                     # one-time installer (Java, Ghidra, PyGhidra, venv)
├── decompile_binary.sh          # headless decompile -> JSON
├── requirements.txt             # web app Python dependencies
└── Scripts/
    ├── DecompileFunctions.py    # Ghidra script: exports functions to JSON
    └── REvAI_GUI.py             # Streamlit web app
```

## Troubleshooting

| Symptom | Fix |
|---|---|
| `$'\r': command not found` | Windows line endings. Run `dos2unix *.sh Scripts/*.py` |
| `Ghidra was not started with PyGhidra` | Use `./decompile_binary.sh`, not `analyzeHeadless` directly |
| Prompted to install PyGhidra into `revai-venv` | Answer **n**, run `deactivate`, then rerun |
| `No JSON was produced` | Look for `SCRIPT ERROR` in the Ghidra output |
| Claude or OpenAI model list won't load | Check your API key and internet access |
| Ollama shows "no models detected" | Run `ollama serve` and check `ollama list` |

## Author

Built by **AJ Raya** · [Cyb3Raya.com](https://cyb3raya.com)
