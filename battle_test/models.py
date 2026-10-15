"""What the model clients have in common, and which one the config asks for.

Two providers: Ollama (models on this machine or the server) and Amazon
Bedrock (hosted models). Both offer the same chat() method, so the pipeline
doesn't know which it's talking to.
"""

from dataclasses import dataclass


class ModelError(RuntimeError):
    """A model call failed in a way the user can act on (not running, no
    credentials, no access to the model). Commands print it and stop."""


@dataclass(frozen=True)
class Usage:
    """Tokens one call used, as the provider reported them."""
    model: str
    input_tokens: int
    output_tokens: int


def add_usage(totals: dict[str, dict[str, int]], used: Usage) -> None:
    """Add one call to a run's totals (model -> calls and tokens)."""
    counts = totals.setdefault(used.model, {"calls": 0, "input_tokens": 0, "output_tokens": 0})
    counts["calls"] += 1
    counts["input_tokens"] += used.input_tokens
    counts["output_tokens"] += used.output_tokens


PROVIDERS =("ollama", "bedrock")


def _client_for(cfg, provider: str):
    """Imported here, not at the top: Bedrock needs boto3, which an
    Ollama-only setup doesn't have to install."""
    if provider == "bedrock":
        from battle_test.bedrock_client import BedrockClient
        return BedrockClient.from_config(cfg)
    from battle_test.ollama_client import OllamaClient
    return OllamaClient.from_config(cfg)


class SplitClient:
    """Sends the checker model's calls to one client and every other call
    to another, so the drafting models and the checker can run on
    different providers. The pipeline sees one client, as before."""

    def __init__(self, drafting, checker, checker_model: str):
        self.drafting = drafting
        self.checker = checker
        self.checker_model = checker_model

    def chat(self, model: str, system: str, user: str, **kwargs) -> str:
        client = self.checker if model == self.checker_model else self.drafting
        return client.chat(model, system, user, **kwargs)


def make_client(cfg):
    """The client for cfg.provider, or a SplitClient when the checker model
    runs on another provider ([models] checker_provider)."""
    drafting = _client_for(cfg, cfg.provider)
    if not cfg.split_providers:
        return drafting
    import dataclasses
    checker_cfg = dataclasses.replace(cfg, provider=cfg.checker_provider,
                                      timeout_seconds=cfg.checker_timeout_seconds or cfg.timeout_seconds)
    return SplitClient(drafting, _client_for(checker_cfg, cfg.checker_provider), cfg.checker_model)
