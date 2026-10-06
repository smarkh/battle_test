"""Amazon Bedrock chat client, with the same chat() method as the Ollama one.

Uses Bedrock's Converse API, which is one request shape for every model
family it hosts (Qwen, Llama, DeepSeek, Mistral, Claude, ...), so comparing
models is a config change.

Credentials never appear here or in a config file. boto3 finds them the
usual way: an AWS profile on the laptop (`aws sso login`), or the instance
role on an AWS server.
"""

import sys
from typing import Callable

from battle_test.models import ModelError, Usage

# Throttled and failed calls are retried with backoff, this many attempts in
# all, and then the run fails. A bound matters here: every attempt that
# reaches a model is billed.
MAX_ATTEMPTS = 4
CONNECT_TIMEOUT_SECONDS = 10

_JSON_ONLY = "\n\nReply with the JSON object only: no other text, and no code fence."

_HINTS = {
    "AccessDeniedException": "This AWS account or role isn't allowed to use that model. Request access to it "
                             "in the Bedrock console, and check the role's permissions.",
    "ResourceNotFoundException": "Bedrock doesn't know that model ID in this region. Check bedrock.models.*.id "
                                 "and bedrock.region.",
    "ValidationException": "Bedrock rejected the request. Some models don't accept a temperature (set "
                           "`temperature = false` for the model) or need a smaller max_tokens.",
    "ThrottlingException": f"Still throttled after {MAX_ATTEMPTS} attempts. Wait, or ask AWS for a higher quota.",
    "ExpiredTokenException": "The AWS sign-in has expired. Run `aws sso login` again.",
    "UnrecognizedClientException": "AWS didn't accept the credentials. Run `aws sso login` again.",
}


class BedrockError(ModelError):
    pass


def _aws_errors() -> tuple:
    try:
        from botocore.exceptions import BotoCoreError, ClientError
    except ImportError:
        return ()
    return (BotoCoreError, ClientError)


def _explain(error: Exception) -> str:
    code = getattr(error, "response", {}).get("Error", {}).get("Code", "")
    hint = _HINTS.get(code)
    if hint is None and type(error).__name__ in ("NoCredentialsError", "ProfileNotFound", "SSOTokenLoadError",
                                                 "UnauthorizedSSOTokenError", "TokenRetrievalError"):
        hint = ("No usable AWS credentials. Sign in with `aws sso login` (and set AWS_PROFILE or bedrock.profile "
                "if you use a named profile).")
    return f"Bedrock: {error}" + (f"\n{hint}" if hint else "")


class BedrockClient:
    def __init__(self, region: str, models: dict, max_tokens: int, temperature: float, *,
                 profile: str = "", timeout_seconds: int = 600, runtime=None):
        """`models` maps the names used in [models] to BedrockModel settings.
        `runtime` replaces the boto3 client (for tests)."""
        self.region = region
        self.models = models
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.profile = profile
        self.timeout_seconds = timeout_seconds
        self._runtime = runtime

    @classmethod
    def from_config(cls, cfg) -> "BedrockClient":
        return cls(cfg.bedrock_region, cfg.bedrock_models, cfg.max_tokens, cfg.temperature,
                   profile=cfg.bedrock_profile, timeout_seconds=cfg.timeout_seconds)

    def _client(self):
        if self._runtime is None:
            try:
                import boto3
                from botocore.config import Config as BotoConfig
            except ImportError as e:
                raise BedrockError("The Bedrock provider needs boto3: pip install -r requirements.txt") from e
            try:
                session = boto3.Session(profile_name=self.profile or None, region_name=self.region)
                self._runtime = session.client("bedrock-runtime", config=BotoConfig(
                    # total_max_attempts counts the first try. botocore's
                    # plain max_attempts counts only the retries after it.
                    retries={"total_max_attempts": MAX_ATTEMPTS, "mode": "adaptive"},
                    connect_timeout=CONNECT_TIMEOUT_SECONDS, read_timeout=self.timeout_seconds))
            except _aws_errors() as e:
                raise BedrockError(_explain(e)) from e
        return self._runtime

    def _request(self, model: str, system: str, user: str, json_mode: bool) -> dict:
        settings = self.models.get(model)
        model_id = settings.id if settings else model  # a name not in the table is used as the ID itself
        if not model_id:
            raise BedrockError(f"bedrock.models.{model}.id is empty. Copy the model's ID (or its inference "
                               "profile ID) from the Bedrock console into the config.")
        inference = {"maxTokens": (settings and settings.max_tokens) or self.max_tokens}
        if settings is None or settings.temperature:
            inference["temperature"] = self.temperature
        return {
            "modelId": model_id,
            "system": [{"text": system}],
            "messages": [{"role": "user", "content": [{"text": user + (_JSON_ONLY if json_mode else "")}]}],
            "inferenceConfig": inference,
        }

    def chat(
        self,
        model: str,
        system: str,
        user: str,
        on_token: Callable[[str], None] | None = None,
        json_mode: bool = False,
        on_usage: Callable[[Usage], None] | None = None,
    ) -> str:
        """Send one system+user exchange and return the full reply.

        Streams, so long drafts show progress via on_token. Bedrock has no
        JSON mode, so json_mode only asks for JSON: the caller must cope
        with a reply that isn't (grounding.py does).
        """
        request = self._request(model, system, user, json_mode)
        parts: list[str] = []
        stop_reason = ""
        try:
            for event in self._client().converse_stream(**request)["stream"]:
                if "contentBlockDelta" in event:
                    # Reasoning models also stream their thinking here, as
                    # "reasoningContent". Only the answer is wanted.
                    piece = event["contentBlockDelta"]["delta"].get("text", "")
                    if piece:
                        parts.append(piece)
                        if on_token:
                            on_token(piece)
                elif "messageStop" in event:
                    stop_reason = event["messageStop"].get("stopReason", "")
                elif "metadata" in event and on_usage:
                    usage = event["metadata"].get("usage", {})
                    on_usage(Usage(model, usage.get("inputTokens", 0), usage.get("outputTokens", 0)))
        except _aws_errors() as e:
            raise BedrockError(_explain(e)) from e

        text = "".join(parts)
        if not text.strip():
            raise BedrockError(f"{model} returned no text (stop reason: {stop_reason or 'unknown'}).")
        if stop_reason == "max_tokens":
            print(f"\n[warning] {model} stopped at its output limit, so this reply is cut short. "
                  "Raise bedrock.max_tokens (or the model's own max_tokens) in the config.", file=sys.stderr)
        return text
