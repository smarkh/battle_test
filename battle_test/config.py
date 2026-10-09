"""Load config.toml into typed settings."""

import os
import tomllib
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from battle_test.models import PROVIDERS

# The laptop uses config.toml next to the code. The server's container sets
# BATTLE_TEST_CONFIG to its own file (config.server.toml), so every command
# (web, users, corpus, evaluate) picks up the server settings.
DEFAULT_CONFIG_PATH = Path(
    os.environ.get("BATTLE_TEST_CONFIG") or Path(__file__).resolve().parent.parent / "config.toml"
)


@dataclass(frozen=True)
class BedrockModel:
    """One Bedrock model, under the short name [models] refers to it by."""
    id: str  # the model ID or inference profile ID, from the Bedrock console
    # US dollars per million tokens, for the cost estimate. None = unknown.
    input_price: float | None = None
    output_price: float | None = None
    max_tokens: int | None = None  # overrides bedrock.max_tokens for this model
    temperature: bool = True  # False for models that reject a temperature


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
    # How long Ollama keeps the model loaded after each request, e.g. "30s".
    # None uses Ollama's default (5 minutes). On the shared server GPU a short
    # value frees the card for Open WebUI soon after a run finishes.
    keep_alive: str | None = None
    provider: str = "ollama"  # or "bedrock"
    bedrock_region: str = ""
    bedrock_profile: str = ""  # an AWS profile name. "" = boto3's default credentials.
    max_tokens: int = 16000  # the most a Bedrock model may write in one reply
    bedrock_models: dict[str, BedrockModel] = field(default_factory=dict)

    def cost(self, usage: dict[str, dict[str, int]]) -> float | None:
        """Estimated US dollars for a run's token usage (model -> counts), or
        None if any model that was used has no price in the config."""
        total = 0.0
        for model, counts in usage.items():
            priced = self.bedrock_models.get(model)
            if priced is None or priced.input_price is None or priced.output_price is None:
                return None
            total += (counts["input_tokens"] * priced.input_price
                      + counts["output_tokens"] * priced.output_price) / 1_000_000
        return total if usage else None


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> Config:
    with open(path, "rb") as f:
        raw = tomllib.load(f)

    rounds = raw["pipeline"]["rounds"]
    if rounds not in (1, 2):
        raise ValueError(f"pipeline.rounds must be 1 or 2, got {rounds}")
    provider = raw["models"].get("provider", "ollama")
    if provider not in PROVIDERS:
        raise ValueError(f"models.provider must be one of {PROVIDERS}, got {provider!r}")
    # Each provider needs only its own section.
    ollama, bedrock = raw.get("ollama", {}), raw.get("bedrock", {})
    if provider == "ollama" and "url" not in ollama:
        raise ValueError(f"{path} has no ollama.url, which the ollama provider needs.")
    if provider == "bedrock" and not bedrock.get("region"):
        raise ValueError(f"{path} has no bedrock.region, which the bedrock provider needs.")

    return Config(
        ollama_url=ollama.get("url", "").rstrip("/"),
        timeout_seconds=(bedrock if provider == "bedrock" else ollama).get("timeout_seconds", 1800),
        plaintiff_model=raw["models"]["plaintiff"],
        defendant_model=raw["models"]["defendant"],
        num_ctx=raw["generation"].get("num_ctx", 12288),
        temperature=raw["generation"]["temperature"],
        rounds=rounds,
        output_dir=_resolve(path, raw["pipeline"]["output_dir"]),
        keep_alive=ollama.get("keep_alive"),
        provider=provider,
        bedrock_region=bedrock.get("region", ""),
        bedrock_profile=bedrock.get("profile", ""),
        max_tokens=bedrock.get("max_tokens", 16000),
        bedrock_models={
            name: BedrockModel(m.get("id", ""), m.get("input_per_million"), m.get("output_per_million"),
                               m.get("max_tokens"), m.get("temperature", True))
            for name, m in bedrock.get("models", {}).items()
        },
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
    # The address users reach the site at, for the setup links the admin
    # command prints. "" means http://host:port (the laptop).
    public_url: str = ""
    # How many cases one user may have waiting or running at once, so nobody
    # fills the queue. 0 = no cap.
    max_active_cases: int = 3
    # How many cases run at the same time. 1 for Ollama, which has one GPU.
    # Bedrock has no such limit, so its config can raise this.
    workers: int = 1

    @property
    def base_url(self) -> str:
        return self.public_url.rstrip("/") or f"http://{self.host}:{self.port}"

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
        config = tomllib.load(f)
    raw = config["web"]
    workers = raw.get("workers", 1)
    if not isinstance(workers, int) or isinstance(workers, bool) or workers < 1:
        raise ValueError(f"web.workers must be a whole number, 1 or more, got {workers!r}")
    if workers > 1 and config.get("models", {}).get("provider", "ollama") == "ollama":
        raise ValueError(f"{path} has web.workers = {workers}, but the ollama provider has one GPU and "
                         "runs one case at a time. Leave web.workers out, or set it to 1.")
    return WebConfig(raw["host"], raw["port"], _resolve(path, raw["data_dir"]),
                     raw.get("secure_cookies", False), raw.get("retention_days", 90),
                     raw.get("behind_proxy", False), raw.get("public_url", ""),
                     raw.get("max_active_cases", 3), workers)


PERIODS = ("month", "total")


@dataclass(frozen=True)
class Plan:
    """A service package: how many cases an account may run, and how often
    that allowance renews."""
    name: str
    label: str
    cases: int | None  # None = no limit
    period: str        # "month": renews on the 1st. "total": never renews (a trial).

    def period_start(self, now: datetime) -> str:
        """Cases started at or after this count against the allowance.
        "" means every case ever."""
        if self.period == "total":
            return ""
        return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat(timespec="seconds")

    def renews_on(self, now: datetime) -> str:
        """The date the allowance next renews, or "" if it never does."""
        if self.period == "total":
            return ""
        return datetime(now.year + now.month // 12, now.month % 12 + 1, 1).strftime("%Y-%m-%d")


@dataclass(frozen=True)
class Plans:
    by_name: dict[str, Plan]
    default: str  # for accounts with no plan, or one that's no longer in the config

    def get(self, name: str) -> Plan:
        return self.by_name.get(name) or self.by_name[self.default]

    def pooled_cases(self, seat_plans: list[str]) -> int | None:
        """The allowance a firm's seats share: the sum of each seat's plan.
        None (no limit) if any seat's plan has none."""
        cases = [self.get(name).cases for name in seat_plans]
        return None if None in cases else sum(cases)


def load_plans(path: Path = DEFAULT_CONFIG_PATH) -> Plans:
    with open(path, "rb") as f:
        raw = tomllib.load(f).get("plans")
    if not raw:
        raise ValueError(f"{path} has no [plans] section, so no account could be given a case allowance.")
    by_name = {}
    for name, value in raw.items():
        if not isinstance(value, dict):
            continue  # "default"
        cases, period = value.get("cases"), value.get("period", "month")
        if cases is not None and (not isinstance(cases, int) or isinstance(cases, bool) or cases < 0):
            raise ValueError(f"plans.{name}.cases must be a whole number (or left out for no limit), got {cases!r}")
        if period not in PERIODS:
            raise ValueError(f"plans.{name}.period must be one of {PERIODS}, got {period!r}")
        by_name[name] = Plan(name, value.get("label", name.capitalize()), cases, period)
    default = raw.get("default")
    if default not in by_name:
        raise ValueError(f"plans.default must name one of the plans ({', '.join(by_name)}), got {default!r}")
    return Plans(by_name, default)


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
