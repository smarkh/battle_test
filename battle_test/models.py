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


PROVIDERS = ("ollama", "bedrock")


def make_client(cfg):
    """The client for cfg.provider. Imported here, not at the top: Bedrock
    needs boto3, which an Ollama-only setup doesn't have to install."""
    if cfg.provider == "bedrock":
        from battle_test.bedrock_client import BedrockClient
        return BedrockClient.from_config(cfg)
    from battle_test.ollama_client import OllamaClient
    return OllamaClient.from_config(cfg)
