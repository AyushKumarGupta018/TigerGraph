"""Provider-agnostic LLM access.

Design principle for this project: the LLM reasons, synthesises and
explains - it never replaces graph analysis. So every LLM call in the
codebase goes through synthesize(), which:

  1. uses the configured provider (OpenAI or Anthropic) when a key is
     available, and
  2. falls back to a deterministic template when it is not, so the whole
     pipeline (tests, benchmark, demo) still runs end to end offline.
"""
from .config import settings


def _build_chat_model():
    """Lazily construct the chat model for the configured provider.

    Returns None when no provider/key is configured - callers must be
    able to live with that (they all can, via the template fallback).
    """
    provider = settings.llm_provider.lower()
    try:
        if provider == "openai":
            from langchain_openai import ChatOpenAI
            return ChatOpenAI(model=settings.llm_model, temperature=0)
        if provider == "anthropic":
            from langchain_anthropic import ChatAnthropic
            return ChatAnthropic(model=settings.llm_model, temperature=0)
        if provider in ("google", "gemini"):
            from langchain_google_genai import ChatGoogleGenerativeAI
            return ChatGoogleGenerativeAI(model=settings.llm_model or "gemini-1.5-flash", temperature=0)
    except Exception:
        # Missing key or package - degrade gracefully rather than crash
        # mid-investigation. The agent notes the fallback in its output.
        return None
    return None


_model = None
_model_initialised = False


def synthesize(instruction: str, context: str) -> str:
    """Ask the LLM to synthesise a narrative from grounded context.

    The context comes from GraphRAG (policy excerpts + live evidence),
    never raw table dumps - that is the whole point of grounding.
    """
    global _model, _model_initialised
    if not _model_initialised:
        _model = _build_chat_model()
        _model_initialised = True

    if _model is not None:
        prompt = (
            "You are a senior fraud investigator writing for a case file. "
            "Use ONLY the evidence and policy context provided. Be precise, "
            "cite specific evidence items, and state remaining uncertainty.\n\n"
            f"TASK: {instruction}\n\nCONTEXT:\n{context}"
        )
        try:
            return _model.invoke(prompt).content
        except Exception:
            pass  # fall through to the deterministic template

    # Deterministic fallback: honest, structured, and clearly labelled.
    return (
        f"[deterministic summary - no LLM configured]\n"
        f"Task: {instruction}\n"
        f"Grounded context used:\n{context}"
    )
