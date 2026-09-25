"""Central configuration.

Everything the agent needs to know about its environment lives here, so
the rest of the codebase never touches os.environ directly. Values come
from a .env file (see .env.example) with offline-friendly defaults so a
fresh clone runs with zero infrastructure.
"""
import os
from dataclasses import dataclass
from pathlib import Path

try:
    # dotenv is a convenience, not a hard requirement (CI may inject env vars)
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def _flag(name: str, default: str = "true") -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes")


@dataclass(frozen=True)
class Settings:
    """Immutable snapshot of the runtime environment."""

    # TigerGraph - when offline_mode is on, the graph client answers the
    # same questions from the sample CSVs instead of a live cluster.
    tg_host: str = os.getenv("TG_HOST", "")
    tg_graph: str = os.getenv("TG_GRAPH", "FraudGraph")
    tg_username: str = os.getenv("TG_USERNAME", "tigergraph")
    tg_password: str = os.getenv("TG_PASSWORD", "")
    tg_secret: str = os.getenv("TG_SECRET", "")
    offline_mode: bool = _flag("OFFLINE_MODE", "true")

    # LLM - "none" gives a fully deterministic run (great for tests and
    # for proving the graph does the analysis, not the model).
    llm_provider: str = os.getenv("LLM_PROVIDER", "none")
    llm_model: str = os.getenv("LLM_MODEL", "gpt-4o")

    # Paths
    data_dir: Path = Path(os.getenv("DATA_DIR", "./data/sample"))
    output_dir: Path = Path(os.getenv("OUTPUT_DIR", "./output"))


settings = Settings()
