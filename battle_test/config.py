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

    return Config(
        ollama_url=raw["ollama"]["url"].rstrip("/"),
        timeout_seconds=raw["ollama"]["timeout_seconds"],
        plaintiff_model=raw["models"]["plaintiff"],
        defendant_model=raw["models"]["defendant"],
        num_ctx=raw["generation"]["num_ctx"],
        temperature=raw["generation"]["temperature"],
        rounds=rounds,
        output_dir=_resolve(path, raw["pipeline"]["output_dir"]),
    )


@dataclass(frozen=True)
class CorpusConfig:
    snapshot: str
    base_url: str
    data_dir: Path
    jurisdictions: tuple[str, ...]
    document_types: tuple[str, ...]

    @property
    def db_path(self) -> Path:
        return self.data_dir / "law.sqlite"

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw" / self.snapshot


def load_corpus_config(path: Path = DEFAULT_CONFIG_PATH) -> CorpusConfig:
    with open(path, "rb") as f:
        raw = tomllib.load(f)["corpus"]
    return CorpusConfig(
        snapshot=raw["snapshot"],
        base_url=raw["base_url"].rstrip("/"),
        data_dir=_resolve(path, raw["data_dir"]),
        jurisdictions=tuple(raw["jurisdictions"]),
        document_types=tuple(raw["document_types"]),
    )


def _resolve(config_path: Path, value: str) -> Path:
    """Resolve a config path relative to the config file's directory."""
    p = Path(value)
    return p if p.is_absolute() else config_path.resolve().parent / p
