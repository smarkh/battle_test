"""Minimal Ollama chat client (standard library only)."""

import json
import sys
import urllib.error
import urllib.request
from typing import Callable

from battle_test.models import ModelError, Usage


class OllamaError(ModelError):
    pass


class OllamaClient:
    def __init__(self, url: str, timeout_seconds: int, num_ctx: int, temperature: float,
                 keep_alive: str | None = None):
        self.url = url
        self.timeout_seconds = timeout_seconds
        self.num_ctx = num_ctx
        self.temperature = temperature
        self.keep_alive = keep_alive

    @classmethod
    def from_config(cls, cfg) -> "OllamaClient":
        return cls(cfg.ollama_url, cfg.timeout_seconds, cfg.num_ctx, cfg.temperature, cfg.keep_alive)

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

        Streams the response so long drafts show progress via on_token.
        json_mode constrains the reply to valid JSON. on_usage is told the
        token counts Ollama reports.
        """
        body = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": True,
            "options": {"num_ctx": self.num_ctx, "temperature": self.temperature},
        }
        if json_mode:
            body["format"] = "json"
        if self.keep_alive is not None:
            body["keep_alive"] = self.keep_alive
        request = urllib.request.Request(
            f"{self.url}/api/chat",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )

        parts: list[str] = []
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                for line in response:
                    if not line.strip():
                        continue
                    chunk = json.loads(line)
                    if "error" in chunk:
                        raise OllamaError(chunk["error"])
                    piece = chunk.get("message", {}).get("content", "")
                    if piece:
                        parts.append(piece)
                        if on_token:
                            on_token(piece)
                    if chunk.get("done"):
                        self._warn_if_context_full(chunk, model)
                        if on_usage:
                            on_usage(Usage(model, chunk.get("prompt_eval_count", 0), chunk.get("eval_count", 0)))
                        break
        except urllib.error.HTTPError as e:
            raise OllamaError(f"Ollama returned HTTP {e.code}: {e.read().decode(errors='replace')}") from e
        except urllib.error.URLError as e:
            raise OllamaError(f"Could not reach Ollama at {self.url} ({e.reason}). Is it running?") from e

        return "".join(parts)

    def _warn_if_context_full(self, final_chunk: dict, model: str) -> None:
        # Ollama silently drops the start of the prompt when it overflows
        # num_ctx, which here would mean losing part of the complaint.
        used = final_chunk.get("prompt_eval_count", 0) + final_chunk.get("eval_count", 0)
        if used >= self.num_ctx * 0.95:
            print(
                f"\n[warning] {model} used {used} of {self.num_ctx} context tokens; "
                "input may have been truncated. Raise generation.num_ctx in config.toml.",
                file=sys.stderr,
            )
