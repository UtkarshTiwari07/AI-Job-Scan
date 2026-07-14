"""
jobscan_llm.py — one provider-agnostic LLM layer for the whole project.

Both LLM touch-points in the pipeline go through here:
  * Phase 2 (Crawl4AI extraction) reads `get_model()` + `resolve_token()` and
    passes them straight into crawl4ai's LLMConfig (crawl4ai routes through
    litellm, so a provider-prefixed model string just works).
  * Phase 4 (evaluation + proposal drafting) calls `chat_completion()`.

Choose your model with ONE env var, in litellm's `provider/model` form::

    LLM_MODEL=deepseek/deepseek-chat        # default (matches the original)
    LLM_MODEL=gemini/gemini-2.0-flash
    LLM_MODEL=openai/gpt-4o-mini
    LLM_MODEL=anthropic/claude-sonnet-4-20250514
    LLM_MODEL=groq/llama-3.3-70b-versatile

Set the API key that matches the provider (DEEPSEEK_API_KEY, OPENAI_API_KEY,
GEMINI_API_KEY, ANTHROPIC_API_KEY, GROQ_API_KEY, …). litellm also reads these
standard names automatically.

`litellm` is imported lazily inside the functions so that `--dry-run` (which
never calls an LLM) works without the package installed.
"""

import os

DEFAULT_MODEL = "deepseek/deepseek-chat"

# litellm provider prefix → environment variable holding that provider's key.
PROVIDER_KEY_ENV = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "groq": "GROQ_API_KEY",
    "mistral": "MISTRAL_API_KEY",
    "together_ai": "TOGETHERAI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "cohere": "COHERE_API_KEY",
    "xai": "XAI_API_KEY",
}


def get_model() -> str:
    """The configured model string, e.g. 'gemini/gemini-2.0-flash'."""
    return (os.getenv("LLM_MODEL") or DEFAULT_MODEL).strip() or DEFAULT_MODEL


def provider_of(model: str = None) -> str:
    """The provider prefix of a model string ('openai' when unprefixed)."""
    model = model or get_model()
    return model.split("/", 1)[0] if "/" in model else "openai"


def resolve_token(model: str = None):
    """Return the API key for the model's provider.

    Falls back to any provider key that happens to be set, which is handy when
    someone uses an OpenAI-compatible base URL with a single generic key.
    """
    model = model or get_model()
    env_name = PROVIDER_KEY_ENV.get(provider_of(model))
    if env_name and os.getenv(env_name):
        return os.getenv(env_name)
    for candidate in PROVIDER_KEY_ENV.values():
        if os.getenv(candidate):
            return os.getenv(candidate)
    return None


def missing_key_hint(model: str = None) -> str:
    model = model or get_model()
    env_name = PROVIDER_KEY_ENV.get(provider_of(model), "the provider's API key")
    return f"Set {env_name} in your .env for LLM_MODEL={model}"


def chat_completion(messages, max_tokens: int = 14000, model: str = None):
    """Provider-agnostic chat completion.

    Returns (content, reasoning) where `reasoning` is any chain-of-thought the
    provider exposed (DeepSeek/others) or None. Raises on hard failure so the
    caller's existing per-batch try/except can log and continue.
    """
    import litellm  # lazy: only needed for live runs

    model = model or get_model()
    api_key = resolve_token(model)

    base_kwargs = dict(model=model, messages=messages, max_tokens=max_tokens)
    if api_key:
        base_kwargs["api_key"] = api_key

    # Preserve the original DeepSeek "thinking" behaviour when available, but
    # degrade gracefully for providers/models that don't accept it.
    attempts = []
    if provider_of(model) == "deepseek":
        attempts.append({**base_kwargs, "extra_body": {"thinking": {"type": "enabled"}}})
    attempts.append(base_kwargs)

    last_err = None
    for kwargs in attempts:
        try:
            resp = litellm.completion(**kwargs)
            msg = resp.choices[0].message
            content = getattr(msg, "content", None) or ""
            reasoning = getattr(msg, "reasoning_content", None)
            return content, reasoning
        except Exception as err:  # try the next (simpler) attempt
            last_err = err
    raise last_err
