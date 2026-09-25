"""Load config.toml into typed settings."""

import tomllib
from dataclasses import dataclass
from pathlib import Path

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.toml"


@dataclass(frozen=True)
class Config:
    ollama_url: str
    timeout_seconds: int
    plaintiff_model: str
    defendant_model: str
    num_ctx: int
    temperature: float
    rounds: int
    output_dir: Path


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> Config:
    with open(path, "rb") as f:
        raw = tomllib.load(f)

    rounds = raw["pipeline"]["rounds"]
    if rounds not in (1, 2):
        raise ValueError(f"pipeline.rounds must be 1 or 2, got {rounds}")

    output_dir = Path(raw["pipeline"]["output_dir"])
    if not output_dir.is_absolute():
        output_dir = path.resolve().parent / output_dir

    return Config(
        ollama_url=raw["ollama"]["url"].rstrip("/"),
        timeout_seconds=raw["ollama"]["timeout_seconds"],
        plaintiff_model=raw["models"]["plaintiff"],
        defendant_model=raw["models"]["defendant"],
        num_ctx=raw["generation"]["num_ctx"],
        temperature=raw["generation"]["temperature"],
        rounds=rounds,
        output_dir=output_dir,
    )
