"""Load config.toml into typed settings."""

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

# The laptop uses config.toml next to the code. The server's container sets
# BATTLE_TEST_CONFIG to its own file (config.server.toml), so every command
# (web, users, corpus, evaluate) picks up the server settings.
DEFAULT_CONFIG_PATH = Path(
    os.environ.get("BATTLE_TEST_CONFIG") or Path(__file__).resolve().parent.parent / "config.toml"
)


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


@dataclass(frozen=True)
class WebConfig:
    host: str
    port: int
    data_dir: Path
    secure_cookies: bool
    retention_days: int  # finished cases are deleted after this; 0 = keep forever
    behind_proxy: bool  # served through Caddy + Cloudflare (the deployed setup)

    def public_serving_problem(self) -> str | None:
        """Why this config may not serve on its host, or None if it may.

        Localhost is always allowed. Any other address (e.g. 0.0.0.0 in the
        server's container) needs both behind_proxy and secure_cookies, so a
        laptop config can't be put on a network by accident.
        """
        if self.host in LOCAL_HOSTS:
            return None
        missing = [name for name, on in (("behind_proxy", self.behind_proxy),
                                         ("secure_cookies", self.secure_cookies)) if not on]
        if missing:
            return (f"web.host is {self.host!r}, which isn't local, so [web] needs "
                    f"{' and '.join(f'{m} = true' for m in missing)}. Serving beyond this machine "
                    "is only for the deployed setup behind Caddy + Cloudflare (HTTPS).")
        return None


LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


def load_web_config(path: Path = DEFAULT_CONFIG_PATH) -> WebConfig:
    with open(path, "rb") as f:
        raw = tomllib.load(f)["web"]
    return WebConfig(raw["host"], raw["port"], _resolve(path, raw["data_dir"]),
                     raw.get("secure_cookies", False), raw.get("retention_days", 90),
                     raw.get("behind_proxy", False))


def _resolve(config_path: Path, value: str) -> Path:
    """Resolve a config path relative to the config file's directory.

    Paths starting with "/" count as absolute everywhere. The server config's
    container paths (/data/...) aren't absolute to Windows, which wants a
    drive letter, but must not be re-rooted when tests read them on the
    laptop.
    """
    p = Path(value)
    if p.is_absolute() or value.startswith("/"):
        return p
    return config_path.resolve().parent / p
