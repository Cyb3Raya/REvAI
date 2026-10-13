import streamlit as st
import json
import os
import re
import requests
import zipfile
import io
from collections import defaultdict
from openai import OpenAI, OpenAIError

try:
    import anthropic
except ImportError:  # app still works for other providers if the SDK is missing
    anthropic = None

# --- Provider names ---
P_ANTHROPIC = "Anthropic (Claude)"
P_OPENAI = "OpenAI API"
P_OLLAMA = "Ollama (local)"
P_COMPAT = "OpenAI-compatible (LM Studio, vLLM, llama.cpp, etc.)"
PROVIDERS = [P_ANTHROPIC, P_OPENAI, P_OLLAMA, P_COMPAT]

# Fallback lists, used only when the live model list can't be fetched
ANTHROPIC_FALLBACK_MODELS = ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5", "claude-fable-5-1"]
OPENAI_FALLBACK_MODELS = ["gpt-5.2", "gpt-5.2-pro", "gpt-5", "gpt-5-mini", "gpt-5-nano", "gpt-4o", "gpt-4o-mini"]
OLLAMA_FALLBACK_MODELS = ["qwen2.5-coder", "llama3.1", "mistral"]

# --- Title ---
st.set_page_config(page_title="REvAI", layout="centered")
st.title("REvAI - AI Function Summarizer")
st.markdown("Lightweight AI agent to summarize and regenerate decompiled functions from Ghidra.")


# --- Helpers: API keys ---
def key_input(label: str, state_key: str, env_var: str, placeholder: str) -> None:
    """Password field for an API key. Kept in this browser session only, falls back to an env var."""
    entered = st.text_input(
        label,
        type="password",
        placeholder=placeholder,
        help="Your key is only stored in this browser session (not saved permanently).",
    )
    if entered:
        st.session_state[state_key] = entered.strip()
        st.success("API key set for this session")
    if not st.session_state.get(state_key):
        env_key = os.getenv(env_var)
        if env_key:
            st.session_state[state_key] = env_key.strip()
            st.info(f"Using {env_var} from environment variable")
    if not st.session_state.get(state_key):
        st.warning("No API key provided - this provider will not work")


# --- Sidebar Settings ---
compat_base_url = os.getenv("OPENAI_COMPAT_BASE_URL", "http://localhost:1234/v1")
ollama_base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")

with st.sidebar:
    st.header("Settings")
    source = st.selectbox("Model Source", PROVIDERS)

    if source == P_ANTHROPIC:
        st.subheader("Anthropic API Key")
        if anthropic is None:
            st.error("The 'anthropic' package is not installed. Re-run setup.sh or: pip install anthropic")
        key_input("Enter your Anthropic API key", "anthropic_api_key", "ANTHROPIC_API_KEY", "sk-ant-...")

    elif source == P_OPENAI:
        st.subheader("OpenAI API Key")
        key_input("Enter your OpenAI API key", "openai_api_key", "OPENAI_API_KEY", "sk-...")

    elif source == P_OLLAMA:
        st.subheader("Ollama Settings")
        ollama_base_url = st.text_input("Ollama base URL", value=ollama_base_url)

    else:  # OpenAI-compatible server
        st.subheader("OpenAI-compatible Server")
        compat_base_url = st.text_input(
            "Server base URL",
            value=compat_base_url,
            help="LM Studio: http://localhost:1234/v1 | vLLM: http://localhost:8000/v1 | llama.cpp: http://localhost:8080/v1",
        )
        compat_key = st.text_input(
            "API key (optional)",
            type="password",
            help="Most local servers don't need one. Stored in this browser session only.",
        )
        st.session_state.compat_api_key = compat_key.strip() if compat_key else os.getenv("OPENAI_COMPAT_API_KEY", "")


# --- Helpers: general ---
def safe_snake_case(name: str, max_len: int = 30) -> str:
    name = re.sub(r"[^a-zA-Z0-9_]+", "_", (name or "").strip().lower())
    name = re.sub(r"_+", "_", name).strip("_")
    return (name or "func_summary")[:max_len]


# --- Ollama ---
def get_ollama_models(base_url: str) -> list[str]:
    try:
        r = requests.get(f"{base_url.rstrip('/')}/api/tags", timeout=5)
        r.raise_for_status()
        data = r.json()
        models = [m.get("name") for m in data.get("models", []) if m.get("name")]
        return sorted(set(models))
    except Exception:
        return []


def call_ollama(base_url: str, model_name: str, prompt: str, temperature: float | None = None) -> tuple[str, dict]:
    payload = {"model": model_name, "prompt": prompt, "stream": False}
    if temperature is not None:
        payload["options"] = {"temperature": temperature}
    r = requests.post(f"{base_url.rstrip('/')}/api/generate", json=payload, timeout=300)
    r.raise_for_status()
    body = r.json()
    usage = {
        "input_tokens": body.get("prompt_eval_count"),
        "output_tokens": body.get("eval_count"),
    }
    return (body.get("response") or "").strip(), usage


# --- OpenAI and OpenAI-compatible servers ---
def get_openai_client(compat: bool = False) -> OpenAI:
    if compat:
        # Local servers usually ignore the key, but the SDK requires a non-empty value
        return OpenAI(base_url=compat_base_url, api_key=st.session_state.get("compat_api_key") or "not-needed")
    if not st.session_state.get("openai_api_key"):
        raise OpenAIError("OpenAI API key not set. Please enter it in the sidebar.")
    return OpenAI(api_key=st.session_state.openai_api_key)


def get_openai_models(compat: bool = False) -> list[str]:
    fallback = [] if compat else OPENAI_FALLBACK_MODELS
    try:
        models = get_openai_client(compat).models.list()
        ids = [m.id for m in getattr(models, "data", []) if getattr(m, "id", None)]
        if not compat:
            ids = sorted(set(ids), key=lambda x: (0 if x.startswith("gpt-") else 1, x))
        return ids or fallback
    except Exception:
        st.warning("Could not fetch live model list - check your key or server URL.")
        return fallback


def is_gpt5_model(model_name: str) -> bool:
    """GPT-5 family models use the Responses API"""
    return model_name.startswith("gpt-5")


def call_openai_responses(model_name: str, prompt: str, temperature: float | None = None, reasoning_effort: str | None = None) -> tuple[str, dict]:
    """Call OpenAI using the Responses API (for GPT-5 models)"""
    client = get_openai_client()
    kwargs = {"model": model_name, "input": [{"role": "user", "content": prompt}]}

    if reasoning_effort:
        kwargs["reasoning"] = {"effort": reasoning_effort}
        # Only GPT-5 models with reasoning_effort="none" support temperature
        if reasoning_effort == "none" and temperature is not None:
            kwargs["temperature"] = temperature
    elif temperature is not None:
        kwargs["temperature"] = temperature

    response = client.responses.create(**kwargs)

    output_text = ""
    try:
        output_text = response.output_text
    except Exception:
        try:
            parts = []
            for item in getattr(response, "output", []) or []:
                for c in getattr(item, "content", []) or []:
                    if getattr(c, "type", None) in ("output_text", "text") and getattr(c, "text", None):
                        parts.append(c.text)
            output_text = "\n".join(parts).strip()
        except Exception:
            output_text = ""

    usage = {}
    u = getattr(response, "usage", None)
    if u:
        usage = {
            "input_tokens": getattr(u, "input_tokens", None),
            "output_tokens": getattr(u, "output_tokens", None),
        }
    return output_text.strip(), usage


def call_openai_chat(model_name: str, prompt: str, temperature: float | None = 0.7, compat: bool = False) -> tuple[str, dict]:
    """Chat Completions API: older OpenAI models and every OpenAI-compatible local server"""
    client = get_openai_client(compat)
    kwargs = {"model": model_name, "messages": [{"role": "user", "content": prompt}]}
    if temperature is not None:
        kwargs["temperature"] = temperature
    response = client.chat.completions.create(**kwargs)

    output_text = (response.choices[0].message.content or "").strip()
    usage = {}
    if response.usage:
        usage = {
            "input_tokens": response.usage.prompt_tokens,
            "output_tokens": response.usage.completion_tokens,
        }
    return output_text, usage


def call_openai(model_name: str, prompt: str, temperature: float | None = None, reasoning_effort: str | None = None) -> tuple[str, dict]:
    """Picks the right OpenAI API based on model family"""
    if is_gpt5_model(model_name):
        return call_openai_responses(model_name, prompt, temperature, reasoning_effort)
    return call_openai_chat(model_name, prompt, temperature if temperature is not None else 0.7)


# --- Anthropic (Claude) ---
def get_anthropic_client():
    if anthropic is None:
        raise RuntimeError("The 'anthropic' package is not installed. Run: pip install anthropic")
    if not st.session_state.get("anthropic_api_key"):
        raise RuntimeError("Anthropic API key not set. Please enter it in the sidebar.")
    return anthropic.Anthropic(api_key=st.session_state.anthropic_api_key)


def get_anthropic_models() -> list[str]:
    try:
        page = get_anthropic_client().models.list(limit=100)
        ids = [m.id for m in getattr(page, "data", []) if getattr(m, "id", None)]
        return ids or ANTHROPIC_FALLBACK_MODELS  # API returns newest first
    except Exception:
        st.warning("Could not fetch live Claude model list (check your API key) - using fallback list.")
        return ANTHROPIC_FALLBACK_MODELS


def call_anthropic(model_name: str, prompt: str, effort: str | None = None) -> tuple[str, dict]:
    """Claude Messages API. Current Claude models use adaptive thinking; 'effort' controls how much work they do."""
    client = get_anthropic_client()
    kwargs = {
        "model": model_name,
        "max_tokens": 16000,
        "messages": [{"role": "user", "content": prompt}],
    }
    if effort:
        # Sent via extra_body so it works even on SDK versions that predate the output_config argument
        kwargs["extra_body"] = {"output_config": {"effort": effort}}
    response = client.messages.create(**kwargs)

    # Keep only the visible answer, skip thinking blocks
    text = "\n".join(b.text for b in response.content if getattr(b, "type", None) == "text").strip()
    usage = {}
    u = getattr(response, "usage", None)
    if u:
        usage = {
            "input_tokens": getattr(u, "input_tokens", None),
            "output_tokens": getattr(u, "output_tokens", None),
        }
    return text, usage


# --- Pricing (USD per 1M tokens). Local models are free; unknown models show no estimate. ---
MODEL_PRICES_PER_1M = {
    # Anthropic
    "claude-fable-5-1": {"input": 10.00, "output": 50.00},
    "claude-opus-5-5": {"input": 4.00, "output": 20.00},
    "claude-sonnet-5-5": {"input": 2.00, "output": 10.00},
    "claude-haiku-5-5": {"input": 0.10, "output": 0.50},  # starting price
    # OpenAI
    "gpt-5.2": {"input": 1.75, "output": 14.00},
    "gpt-5.2-pro": {"input": 21.00, "output": 168.00},
    "gpt-5": {"input": 1.25, "output": 10.00},
    "gpt-5-mini": {"input": 0.25, "output": 2.00},
    "gpt-5-nano": {"input": 0.05, "output": 0.40},
    "gpt-4o": {"input": 2.50, "output": 10.00},
    "gpt-4o-mini": {"input": 0.150, "output": 0.600},
    "gpt-4-turbo": {"input": 10.00, "output": 30.00},
    "gpt-3.5-turbo": {"input": 0.50, "output": 1.50},
}


def estimate_cost(model_name: str, tokens_in: int | None, tokens_out: int | None) -> float | None:
    if tokens_in is None or tokens_out is None:
        return None
    p = MODEL_PRICES_PER_1M.get(model_name)
    if not p:
        return None
    return (tokens_in / 1_000_000 * p["input"]) + (tokens_out / 1_000_000 * p["output"])


def run_model(prompt: str, temperature: float | None, effort: str | None) -> tuple[str, dict]:
    """Single entry point: sends a prompt to whichever provider is selected"""
    if source == P_ANTHROPIC:
        return call_anthropic(model, prompt, effort)
    if source == P_OPENAI:
        return call_openai(model, prompt, temperature, effort)
    if source == P_OLLAMA:
        return call_ollama(ollama_base_url, model, prompt, temperature)
    return call_openai_chat(model, prompt, temperature, compat=True)


def light_effort() -> str | None:
    """Lower effort for small side tasks (renaming, code regen) to save time and tokens"""
    if source == P_ANTHROPIC:
        return "low"
    if source == P_OPENAI and is_gpt5_model(model):
        return "low"
    return None


# --- Upload JSON ---
json_file = st.file_uploader("Upload your Ghidra JSON file", type=["json"])
if json_file:
    data = json.load(json_file)
    st.success(f"Loaded {len(data)} functions from JSON")

    # --- Model selection (source-specific) ---
    temperature = 0.4
    reasoning_effort = None
    model = None

    def refresh_row(cache_key: str, fetch, label: str) -> None:
        col_a, col_b = st.columns([1, 1])
        with col_a:
            if st.button(f"Refresh {label} model list"):
                with st.spinner("Fetching models..."):
                    st.session_state[cache_key] = fetch()
                st.success("Model list refreshed!")
        with col_b:
            st.caption("Tip: Refresh if you have access to new models.")

    if source == P_ANTHROPIC:
        with st.expander("Claude model options", expanded=True):
            refresh_row("anthropic_model_cache", get_anthropic_models, "Claude")
            models = st.session_state.get("anthropic_model_cache") or ANTHROPIC_FALLBACK_MODELS
            model = st.selectbox("Choose a model", models, index=0)
            reasoning_effort = st.selectbox(
                "Effort",
                ["low", "medium", "high", "xhigh", "max"],
                index=1,
                help="How much work Claude puts into each answer. Higher = more thorough but slower and more tokens. "
                     "Not every model supports xhigh/max.",
            )
            temperature = None
            st.caption("Claude models think adaptively; effort replaces the temperature setting here.")

    elif source == P_OPENAI:
        with st.expander("OpenAI model options", expanded=True):
            refresh_row("openai_model_cache", get_openai_models, "OpenAI")
            models = st.session_state.get("openai_model_cache") or OPENAI_FALLBACK_MODELS
            model = st.selectbox("Choose a model", models, index=0)

            if is_gpt5_model(model):
                st.info("GPT-5 models use the Responses API with reasoning capabilities")
                reasoning_options = ["none", "minimal", "low", "medium", "high"]
                if "pro" in model.lower():
                    reasoning_options.append("xhigh")
                reasoning_effort = st.selectbox(
                    "Reasoning effort",
                    reasoning_options,
                    index=3,
                    help="Controls how much the model thinks before responding. Higher = more accurate but slower and more tokens.",
                )
                if reasoning_effort == "none":
                    temperature = st.slider("Temperature", 0.0, 1.0, 0.4, 0.05)
                else:
                    temperature = None
                    st.info("Temperature is disabled when reasoning effort is not 'none'")
            else:
                temperature = st.slider("Temperature", 0.0, 1.0, 0.4, 0.05)

    elif source == P_OLLAMA:
        with st.expander("Ollama model options", expanded=True):
            local_models = get_ollama_models(ollama_base_url)
            if not local_models:
                st.warning("No models detected (is Ollama running?). Falling back to common names.")
                local_models = OLLAMA_FALLBACK_MODELS
            model = st.selectbox("Choose a local model", local_models)
            temperature = st.slider("Temperature", 0.0, 1.0, 0.4, 0.05)

    else:  # OpenAI-compatible server
        with st.expander("Server model options", expanded=True):
            server_models = get_openai_models(compat=True)
            if server_models:
                model = st.selectbox("Choose a model", server_models)
            else:
                st.warning("Could not list models from the server. Enter the model name manually.")
                model = st.text_input("Model name")
            temperature = st.slider("Temperature", 0.0, 1.0, 0.4, 0.05)

    # --- Explanation and Regeneration toggles ---
    explain_steps = st.checkbox("Include step-by-step explanation (high level)")
    regen_c_code = st.checkbox("Attempt Code Regeneration from Summary")

    # --- Prompt Template ---
    default_prompt = """Analyze the following C function and provide a one-paragraph summary of its purpose.
```c
{code}
```"""

    explain_prompt = """Analyze the following C function. First provide a high-level step-by-step explanation (no hidden reasoning, just the key steps). Then provide a one-paragraph summary of its overall purpose.
```c
{code}
```"""

    prompt_template = explain_prompt if explain_steps else default_prompt

    # --- Function Selector ---
    function_names = [f.get("name", "unknown") for f in data]
    default_selection = ["main"] if "main" in function_names else []
    selected_funcs = st.multiselect("Select functions to summarize", function_names, default=default_selection)

    # --- Session state for results ---
    if "cached_summaries" not in st.session_state:
        st.session_state.cached_summaries = []
        st.session_state.cached_renames = []
        st.session_state.cached_c_code = []
        st.session_state.token_total = 0
        st.session_state.cost_total = 0.0
        st.session_state.last_source = None

    if st.button("Generate Summaries", disabled=not model):
        with st.spinner("Generating summaries..."):
            summaries = []
            rename_map = []
            regenerated_code = []
            name_counts = defaultdict(int)
            totals = {"tokens": 0, "cost": 0.0}

            def track(usage: dict) -> None:
                t_in, t_out = usage.get("input_tokens"), usage.get("output_tokens")
                if isinstance(t_in, int) and isinstance(t_out, int):
                    totals["tokens"] += t_in + t_out
                    est = estimate_cost(model, t_in, t_out)
                    if est is not None:
                        totals["cost"] += est

            def generate_function_name(summary_text: str) -> str:
                rename_prompt = (
                    "Suggest a concise function name based on the following summary. "
                    "Use snake_case and reflect the main purpose. "
                    "Output ONLY the function name.\n\n"
                    f"Summary: {summary_text.strip()}"
                )
                try:
                    suggestion, usage = run_model(rename_prompt, 0.2, light_effort())
                    track(usage)
                    suggestion = safe_snake_case(suggestion)
                    name_counts[suggestion] += 1
                    if name_counts[suggestion] > 1:
                        return f"{suggestion}_{name_counts[suggestion]}"
                    return suggestion
                except Exception:
                    return "func_summary"

            for func in data:
                if func.get("name") not in selected_funcs:
                    continue

                code = func.get("decompiled", "")
                if not code or code == "Failed to decompile":
                    st.warning(f"Skipping {func.get('name', 'unknown')} - no usable decompiled code.")
                    continue

                name = func.get("name", "unknown")
                prompt = prompt_template.replace("{code}", code)

                try:
                    answer, usage = run_model(prompt, temperature, reasoning_effort)
                    track(usage)
                    summaries.append({"name": name, "summary": answer})

                    new_func_name = generate_function_name(answer)
                    rename_map.append({"old_name": name, "new_name": new_func_name})

                    if regen_c_code:
                        regen_prompt = (
                            "Using this summary, recreate an approximate C function. Output only C code.\n\n"
                            f"Summary: {answer.strip()}"
                        )
                        regen_code, usage = run_model(regen_prompt, 0.3, light_effort())
                        track(usage)
                        regenerated_code.append({"name": name, "code": regen_code})

                except Exception as e:
                    summaries.append({"name": name, "summary": f"Error: {str(e)}"})

            st.session_state.cached_summaries = summaries
            st.session_state.cached_renames = rename_map
            st.session_state.cached_c_code = regenerated_code
            st.session_state.token_total = totals["tokens"]
            st.session_state.cost_total = totals["cost"]
            st.session_state.last_source = source

        st.success("Summaries generated!")

    # --- Display results ---
    if st.session_state.cached_summaries:
        st.markdown("---")
        st.markdown("### Summary Results")

        for entry in st.session_state.cached_summaries:
            with st.expander(f"Function: {entry['name']}"):
                st.markdown(entry["summary"])

                regen_entry = next((x for x in st.session_state.cached_c_code if x["name"] == entry["name"]), None)
                if regen_entry:
                    st.markdown("**Reconstructed C Function:**")
                    st.code(regen_entry["code"], language="c")

        # Downloads
        with st.expander("Download Summaries JSON"):
            st.download_button(
                "Download",
                json.dumps(st.session_state.cached_summaries, indent=2),
                file_name="summaries.json",
                mime="application/json",
            )

        with st.expander("Download Auto-Rename Map for Ghidra"):
            st.download_button(
                "Download Rename Map",
                json.dumps(st.session_state.cached_renames, indent=2),
                file_name="rename_map.json",
                mime="application/json",
            )

        if regen_c_code and st.session_state.cached_c_code:
            with st.expander("Download Reconstructed C Code"):
                st.download_button(
                    "Download C Code",
                    json.dumps(st.session_state.cached_c_code, indent=2),
                    file_name="reconstructed_functions.json",
                    mime="application/json",
                )

        # ZIP download
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w") as zipf:
            zipf.writestr("summaries.json", json.dumps(st.session_state.cached_summaries, indent=2))
            zipf.writestr("rename_map.json", json.dumps(st.session_state.cached_renames, indent=2))
            if st.session_state.cached_c_code:
                zipf.writestr("reconstructed_functions.json", json.dumps(st.session_state.cached_c_code, indent=2))
        zip_buffer.seek(0)

        st.download_button(
            "Download All (ZIP)",
            data=zip_buffer,
            file_name="revai_output.zip",
            mime="application/zip",
        )

        # Token and cost display
        used = st.session_state.get("last_source") or source
        tokens = st.session_state.token_total
        if used in (P_OLLAMA, P_COMPAT):
            msg = f"Completed using a local model ({used})"
            st.success(f"{msg} | Total Tokens: {tokens} | Cost: $0 (local)" if tokens else msg)
        elif tokens:
            if st.session_state.cost_total > 0:
                st.success(f"Total Tokens: {tokens} | Estimated Cost: ${st.session_state.cost_total:.6f}")
            else:
                st.success(f"Total Tokens: {tokens} | Estimated Cost: (not available for this model)")
        else:
            st.success(f"Completed using {used}")
