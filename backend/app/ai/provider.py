"""The one place a model is called.

Every feature that wants a proposal builds an `AiRequest` and hands it to
`get_provider().complete()`. The provider owns model selection, timeouts,
retries for transport failures, concurrency, in-flight de-duplication,
usage collection and error normalisation. Nothing else imports the SDK.

`ClaudeCodeProvider` (the default) runs the Claude Code CLI on the Claude
subscription signed in on the server -- no API key. `ClaudeProvider`
(Anthropic SDK) and `OpenAiProvider` (OpenAI SDK) call the vendors' APIs with
a key. Each uses JSON-schema output, so what comes back is either a document
matching the task's schema or a normalised error; `AI_PROVIDER` picks one. `NullProvider` answers "insufficient evidence" to everything and
is what runs when AI_ENABLED is false -- the deterministic pipeline must be
complete with it. Tests use `RecordingProvider`.
"""

from __future__ import annotations

import base64
import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from app.ai import guard
from app.core.config import get_settings

# Bumped when the wording of a system prompt changes in a way that could
# change answers; part of every cache key.
PROMPT_VERSION = "2026-09-15.1"


@dataclass(frozen=True)
class TextPart:
    label: str
    text: str


@dataclass(frozen=True)
class ImagePart:
    label: str
    png: bytes


@dataclass
class AiRequest:
    task: str
    system: str
    parts: list[TextPart | ImagePart]
    schema: dict[str, Any]
    max_output_tokens: int
    tier: str = "small"          # "small" | "standard"
    timeout_s: float | None = None
    idempotency_key: str = ""


@dataclass
class Usage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_input_tokens: int | None = None
    reasoning_tokens: int | None = None


@dataclass
class AiResponse:
    data: dict[str, Any] | None
    usage: Usage = field(default_factory=Usage)
    model: str = ""
    latency_ms: int = 0
    # "transport" | "rate_limit" | "quota" | "invalid_response" | "refused" | "auth" | None
    error: str | None = None
    error_detail: str | None = None
    raw_text: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.data is not None

    @property
    def retryable(self) -> bool:
        # A spent balance and a bad credential are not worth retrying: they
        # answer the same on the next call and only cost latency.
        return self.error in ("transport", "rate_limit")


class AiProvider(Protocol):
    name: str

    def complete(self, request: AiRequest) -> AiResponse: ...

    @property
    def ready(self) -> bool:
        """Whether a call could actually be made. False means the feature is
        on but something it needs -- a credential -- is missing, which the
        pages say plainly rather than discovering at call time."""
        ...

    @property
    def status(self) -> str: ...


def estimate_input_tokens(request: AiRequest) -> int:
    """A pre-call estimate for budget reservation: roughly four characters
    per text token, and a flat allowance per image (a small crop is a few
    hundred tokens; the provider reports the real figure afterwards)."""
    total = len(request.system) // 4 + 200   # schema + framing
    for part in request.parts:
        if isinstance(part, TextPart):
            total += len(part.text) // 4 + 8
        else:
            total += 1_200
    return total


class NullProvider:
    """No model. Every request is answered as insufficient evidence."""

    name = "null"
    ready = False
    status = "AI assistance is disabled (AI_ENABLED=false)"

    def complete(self, request: AiRequest) -> AiResponse:
        return AiResponse(
            data={"task_id": request.task, "status": "insufficient_evidence", "proposed_changes": [],
                  "source_references": [], "unresolved_issues": ["AI assistance is disabled"]},
            model="null",
        )


class RecordingProvider:
    """A scripted provider for tests: answers from a queue, records what it
    was asked, counts calls, and can be told to fail or to sleep."""

    name = "recording"
    ready = True
    status = "a scripted provider (tests)"

    def __init__(self, answers: list[dict | Exception] | None = None, delay_s: float = 0.0):
        self.answers = list(answers or [])
        self.requests: list[AiRequest] = []
        self.calls = 0
        self.delay_s = delay_s
        self._lock = threading.Lock()

    def complete(self, request: AiRequest) -> AiResponse:
        with self._lock:
            self.calls += 1
            self.requests.append(request)
            answer = self.answers.pop(0) if self.answers else {"task_id": request.task, "status": "insufficient_evidence",
                                                               "proposed_changes": [], "source_references": [],
                                                               "unresolved_issues": ["no scripted answer"]}
        if self.delay_s:
            time.sleep(self.delay_s)
        if isinstance(answer, Exception):
            return AiResponse(data=None, error=getattr(answer, "kind", "transport"), error_detail=str(answer), model="recording")
        return AiResponse(data=answer, usage=Usage(input_tokens=500, output_tokens=40), model="recording", latency_ms=1)


class ClaudeProvider:
    """Claude through the official SDK, JSON-schema constrained.

    - Model per tier from settings; no date suffixes are appended.
    - Thinking is left at the model's default (adaptive); depth is
      `AI_EFFORT`, low for these short reading tasks.
    - Transport and rate-limit errors are retried by the SDK itself
      (`max_retries`); `refusal` stop reasons and unparsable output are
      reported, not retried here -- the caller decides.
    - Usage fields are copied as the SDK reports them; absent ones stay None.
    """

    name = "claude"

    def __init__(self) -> None:
        import anthropic  # imported only when the provider is built

        settings = get_settings()
        self._anthropic = anthropic
        self._client = anthropic.Anthropic(timeout=settings.ai_timeout_s, max_retries=settings.ai_max_retries)
        self._models = {"small": settings.ai_model_small, "standard": settings.ai_model_standard}
        self._effort = settings.ai_effort
        self._semaphore = threading.BoundedSemaphore(max(1, settings.ai_max_concurrency))
        # The SDK resolves a credential from the environment (an API key, an
        # auth token, or a signed-in profile) and does not complain until the
        # first call, where it raises a plain TypeError. Ask once, here, so
        # the pages can say "no credential" instead of failing mid-read.
        self._credential = bool(getattr(self._client, "api_key", None) or getattr(self._client, "auth_token", None))

    @property
    def ready(self) -> bool:
        return self._credential

    @property
    def status(self) -> str:
        if self._credential:
            return f"Claude, model {self._models['small']}"
        return "AI is enabled but no credential was found: set ANTHROPIC_API_KEY in the server's environment"

    def _content(self, request: AiRequest) -> list[dict[str, Any]]:
        blocks: list[dict[str, Any]] = []
        for part in request.parts:
            if isinstance(part, ImagePart):
                blocks.append({"type": "text", "text": f"[{part.label}]"})
                blocks.append({
                    "type": "image",
                    "source": {"type": "base64", "media_type": "image/png",
                               "data": base64.standard_b64encode(part.png).decode("ascii")},
                })
            else:
                # Document text is data. It is fenced and labelled so that
                # anything written inside a document reads as content, not
                # as an instruction.
                blocks.append({"type": "text", "text": guard.fence(part.label, part.text)})
        return blocks

    def complete(self, request: AiRequest) -> AiResponse:
        anthropic = self._anthropic
        model = self._models.get(request.tier, self._models["small"])
        if not self._credential:
            return AiResponse(data=None, error="auth", error_detail=self.status, model=model)
        started = time.perf_counter()
        client = self._client
        if request.timeout_s:
            client = client.with_options(timeout=request.timeout_s)
        try:
            with self._semaphore:
                message = client.messages.create(
                    model=model,
                    max_tokens=request.max_output_tokens,
                    system=request.system,
                    messages=[{"role": "user", "content": self._content(request)}],
                    output_config={"format": {"type": "json_schema", "schema": request.schema},
                                   "effort": self._effort},
                )
        except anthropic.AuthenticationError as exc:
            return AiResponse(data=None, error="auth", error_detail=str(exc), model=model)
        except anthropic.RateLimitError as exc:
            return AiResponse(data=None, error="rate_limit", error_detail=str(exc), model=model)
        except anthropic.APIStatusError as exc:
            kind = "transport" if exc.status_code >= 500 else "invalid_response"
            return AiResponse(data=None, error=kind, error_detail=str(exc), model=model)
        except (anthropic.APIConnectionError, anthropic.APITimeoutError) as exc:
            return AiResponse(data=None, error="transport", error_detail=str(exc), model=model)
        except Exception as exc:  # noqa: BLE001 -- an unreadable request must not take a page down
            return AiResponse(data=None, error="invalid_response", error_detail=str(exc), model=model)

        latency = int((time.perf_counter() - started) * 1000)
        usage = Usage()
        raw_usage = getattr(message, "usage", None)
        if raw_usage is not None:
            usage.input_tokens = getattr(raw_usage, "input_tokens", None)
            usage.output_tokens = getattr(raw_usage, "output_tokens", None)
            usage.cached_input_tokens = getattr(raw_usage, "cache_read_input_tokens", None)
            details = getattr(raw_usage, "output_tokens_details", None)
            usage.reasoning_tokens = getattr(details, "reasoning_tokens", None) if details is not None else None

        if getattr(message, "stop_reason", None) == "refusal":
            return AiResponse(data=None, usage=usage, model=model, latency_ms=latency, error="refused",
                              error_detail="the model declined the request")
        text = next((b.text for b in message.content if getattr(b, "type", "") == "text"), None)
        if text is None:
            return AiResponse(data=None, usage=usage, model=model, latency_ms=latency, error="invalid_response",
                              error_detail="no text block in the reply")
        try:
            data = json.loads(text)
        except ValueError as exc:
            return AiResponse(data=None, usage=usage, model=model, latency_ms=latency, error="invalid_response",
                              error_detail=f"reply is not JSON: {exc}", raw_text=text)
        if getattr(message, "stop_reason", None) == "max_tokens":
            return AiResponse(data=None, usage=usage, model=model, latency_ms=latency, error="invalid_response",
                              error_detail="reply was cut off at max_tokens", raw_text=text)
        return AiResponse(data=data, usage=usage, model=model, latency_ms=latency, raw_text=text)


class OpenAiProvider:
    """GPT through the official OpenAI SDK, JSON-schema constrained.

    The same contract as `ClaudeProvider`: a request in, a document matching
    the task's schema or a normalised error out. Two differences the SDK
    forces:

    - **The token parameter moved.** Recent models take
      `max_completion_tokens`; older ones take `max_tokens`. Which one a
      given model wants could not be confirmed against the live API (the
      account had no credits when this was written), so the first call for a
      model tries the newer name and falls back once, remembering the answer.
    - **A spent balance is its own error.** The API reports
      `insufficient_quota` as a 429, which would otherwise be retried as a
      rate limit for as long as the budget allowed. It is reported as
      `quota`, and `quota` is never retried.
    """

    name = "openai"
    base_url: str | None = None

    def __init__(self) -> None:
        import openai  # imported only when the provider is built

        settings = get_settings()
        self._openai = openai
        key = self._key(settings)
        self._models = {"small": settings.ai_model_small, "standard": settings.ai_model_standard}
        self._semaphore = threading.BoundedSemaphore(max(1, settings.ai_max_concurrency))
        # This SDK refuses to build a client at all without a credential,
        # where Anthropic's waits until the first call. Catch that here so
        # "no key" is an answer the pages can show rather than an exception
        # raised while a sheet is being read.
        try:
            self._client = openai.OpenAI(api_key=key, base_url=self.base_url, timeout=settings.ai_timeout_s,
                                         max_retries=settings.ai_max_retries)
            self._credential = bool(key or getattr(self._client, "api_key", None))
        except openai.OpenAIError:
            self._client = None
            self._credential = False
        # model id -> the token parameter it accepts, learned on first use.
        self._token_arg: dict[str, str] = {}

    @staticmethod
    def _key(settings) -> str | None:
        return (settings.ai_api_key or "").strip() or None

    @property
    def ready(self) -> bool:
        return self._credential

    @property
    def status(self) -> str:
        if self._credential:
            return f"OpenAI, model {self._models['small']}"
        return ("AI is enabled but no credential was found: set AI_API_KEY in backend/.env "
                "or OPENAI_API_KEY in the server's environment")

    def _messages(self, request: AiRequest) -> list[dict[str, Any]]:
        content: list[dict[str, Any]] = []
        for part in request.parts:
            if isinstance(part, ImagePart):
                content.append({"type": "text", "text": f"[{part.label}]"})
                url = "data:image/png;base64," + base64.standard_b64encode(part.png).decode("ascii")
                content.append({"type": "image_url", "image_url": {"url": url}})
            else:
                # Document text is data, fenced and labelled so that anything
                # written inside a document reads as content, not instruction.
                content.append({"type": "text", "text": guard.fence(part.label, part.text)})
        return [{"role": "system", "content": request.system}, {"role": "user", "content": content}]

    def _create(self, model: str, request: AiRequest):
        """One call, with the token parameter this model accepts."""
        openai = self._openai
        order = [self._token_arg[model]] if model in self._token_arg else ["max_completion_tokens", "max_tokens"]
        last: Exception | None = None
        for token_arg in order:
            try:
                response = self._client.chat.completions.create(
                    model=model,
                    messages=self._messages(request),
                    response_format={"type": "json_schema",
                                     "json_schema": {"name": "proposal", "schema": request.schema, "strict": True}},
                    **{token_arg: request.max_output_tokens},
                )
                self._token_arg[model] = token_arg
                return response
            except openai.BadRequestError as exc:
                # Only a complaint about the token parameter earns another try.
                if "max_tokens" in str(exc) or "max_completion_tokens" in str(exc):
                    last = exc
                    continue
                raise
        raise last if last is not None else RuntimeError("no call was attempted")

    def complete(self, request: AiRequest) -> AiResponse:
        openai = self._openai
        model = self._models.get(request.tier, self._models["small"])
        if not self._credential:
            return AiResponse(data=None, error="auth", error_detail=self.status, model=model)
        started = time.perf_counter()
        try:
            with self._semaphore:
                response = self._create(model, request)
        except openai.AuthenticationError as exc:
            return AiResponse(data=None, error="auth", error_detail=str(exc), model=model)
        except openai.RateLimitError as exc:
            spent = "insufficient_quota" in str(exc) or "credit_balance_exhausted" in str(exc)
            detail = ("the account has no credits left; add billing at platform.openai.com and try again"
                      if spent else str(exc))
            return AiResponse(data=None, error="quota" if spent else "rate_limit", error_detail=detail, model=model)
        except (openai.APIConnectionError, openai.APITimeoutError) as exc:
            return AiResponse(data=None, error="transport", error_detail=str(exc), model=model)
        except openai.APIStatusError as exc:
            kind = "transport" if exc.status_code >= 500 else "invalid_response"
            return AiResponse(data=None, error=kind, error_detail=str(exc), model=model)
        except Exception as exc:  # noqa: BLE001 -- an unreadable request must not take a page down
            return AiResponse(data=None, error="invalid_response", error_detail=str(exc), model=model)

        latency = int((time.perf_counter() - started) * 1000)
        usage = Usage()
        raw = getattr(response, "usage", None)
        if raw is not None:
            usage.input_tokens = getattr(raw, "prompt_tokens", None)
            usage.output_tokens = getattr(raw, "completion_tokens", None)
            prompt_details = getattr(raw, "prompt_tokens_details", None)
            usage.cached_input_tokens = getattr(prompt_details, "cached_tokens", None) if prompt_details else None
            out_details = getattr(raw, "completion_tokens_details", None)
            usage.reasoning_tokens = getattr(out_details, "reasoning_tokens", None) if out_details else None

        choice = response.choices[0] if response.choices else None
        finish = getattr(choice, "finish_reason", None) if choice else None
        if finish == "content_filter":
            return AiResponse(data=None, usage=usage, model=model, latency_ms=latency, error="refused",
                              error_detail="the request was filtered")
        text = getattr(getattr(choice, "message", None), "content", None) if choice else None
        if not text:
            detail = "the reply was cut off at the output limit" if finish == "length" else "no content in the reply"
            return AiResponse(data=None, usage=usage, model=model, latency_ms=latency,
                              error="invalid_response", error_detail=detail)
        if finish == "length":
            return AiResponse(data=None, usage=usage, model=model, latency_ms=latency, error="invalid_response",
                              error_detail="the reply was cut off at the output limit", raw_text=text)
        try:
            data = json.loads(text)
        except ValueError as exc:
            return AiResponse(data=None, usage=usage, model=model, latency_ms=latency, error="invalid_response",
                              error_detail=f"reply is not JSON: {exc}", raw_text=text)
        return AiResponse(data=data, usage=usage, model=model, latency_ms=latency, raw_text=text)


class ClaudeCodeProvider:
    """Claude through the Claude Code CLI, on the subscription it is signed in with.

    No API key: the server runs the `claude` program installed on this machine
    (`AI_CLAUDE_CLI`, default `claude` on the PATH) headless, once per request,
    and Claude Code bills the call to the Claude subscription that is signed in
    there. The same contract as the other providers -- a request in, a document
    matching the task's schema or a normalised error out:

    - The task's JSON schema goes to `--json-schema`; the reply's
      `structured_output` is the document.
    - The task's system prompt replaces Claude Code's own (`--system-prompt`),
      so a call carries only what the task needs.
    - Images are written to a private temporary folder the call runs in, and
      Claude reads them with the Read tool -- the only tool it is given, and
      only when there is an image. With no image it has no tools at all.
    - The prompt goes in on stdin: Windows caps a command line at 32,767
      characters and a batch of clauses is longer than that.
    - `ANTHROPIC_API_KEY` is removed from the call's environment, so the CLI
      uses the subscription even on a machine that also has a key set.
    - Sessions are not saved (`--no-session-persistence`).

    Checked against Claude Code 2.1.263 on 2026-09-14.
    """

    name = "claude-code"

    def __init__(self) -> None:
        import shutil

        settings = get_settings()
        configured = (settings.ai_claude_cli or "claude").strip()
        self._cli = shutil.which(configured) or (configured if Path(configured).is_file() else None)
        self._models = {"small": settings.ai_model_small, "standard": settings.ai_model_standard}
        self._timeout = settings.ai_cli_timeout_s
        self._semaphore = threading.BoundedSemaphore(max(1, settings.ai_max_concurrency))

    @property
    def ready(self) -> bool:
        return self._cli is not None

    @property
    def status(self) -> str:
        if self._cli:
            return f"Claude subscription through Claude Code, model {self._models['small']}"
        return ("AI is enabled but Claude Code was not found: install it and sign in with `claude` on this server, "
                "or set AI_CLAUDE_CLI to the path of claude.exe")

    @staticmethod
    def _prompt(request: AiRequest, images: list[tuple[str, str]]) -> str:
        lines = []
        for part in request.parts:
            if isinstance(part, TextPart):
                # Document text is data, fenced and labelled so that anything
                # written inside a document reads as content, not instruction.
                lines.append(guard.fence(part.label, part.text))
        for label, filename in images:
            lines.append(f"[{label}] is the image file {filename} in the current directory: read it with the Read tool.")
        lines.append("Answer through the structured output only.")
        return "\n\n".join(lines)

    @staticmethod
    def _error_kind(text: str) -> str:
        lowered = text.lower()
        if any(s in lowered for s in ("not logged in", "please run /login", "invalid api key", "authentication", "oauth")):
            return "auth"
        if any(s in lowered for s in ("usage limit", "rate limit", "rate_limit", "overloaded", "limit reached")):
            return "rate_limit"
        return "invalid_response"

    def complete(self, request: AiRequest) -> AiResponse:
        import os
        import subprocess
        import tempfile

        model = self._models.get(request.tier, self._models["small"])
        if not self._cli:
            return AiResponse(data=None, error="auth", error_detail=self.status, model=model)
        started = time.perf_counter()
        with tempfile.TemporaryDirectory(prefix="ep-ai-") as folder:
            images = []
            for index, part in enumerate(p for p in request.parts if isinstance(p, ImagePart)):
                filename = f"image-{index + 1}.png"
                (Path(folder) / filename).write_bytes(part.png)
                images.append((part.label, filename))
            args = [self._cli, "-p", "--output-format", "json", "--model", model,
                    "--system-prompt", request.system, "--json-schema", json.dumps(request.schema),
                    "--no-session-persistence", "--disable-slash-commands", "--strict-mcp-config"]
            args += ["--tools", "Read", "--allowedTools", "Read"] if images else ["--tools", ""]
            env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")}
            try:
                with self._semaphore:
                    completed = subprocess.run(
                        args, input=self._prompt(request, images), capture_output=True, text=True, encoding="utf-8",
                        errors="replace", cwd=folder, env=env, timeout=request.timeout_s or self._timeout,
                    )
            except subprocess.TimeoutExpired:
                return AiResponse(data=None, error="transport", model=model,
                                  error_detail=f"Claude Code did not answer within {request.timeout_s or self._timeout:.0f} s")
            except OSError as exc:
                return AiResponse(data=None, error="transport", error_detail=f"Claude Code could not be started: {exc}", model=model)
        latency = int((time.perf_counter() - started) * 1000)

        try:
            reply = json.loads(completed.stdout)
        except ValueError:
            detail = (completed.stderr or completed.stdout or "no output").strip()[:500]
            return AiResponse(data=None, error=self._error_kind(detail), error_detail=detail, model=model, latency_ms=latency)
        raw_usage = reply.get("usage") or {}
        usage = Usage(
            input_tokens=sum(int(raw_usage.get(k) or 0) for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")),
            output_tokens=raw_usage.get("output_tokens"),
            cached_input_tokens=raw_usage.get("cache_read_input_tokens"),
            reasoning_tokens=(raw_usage.get("output_tokens_details") or {}).get("thinking_tokens"),
        )
        used = next((name for name in (reply.get("modelUsage") or {}) if "haiku" not in name), None) or model
        if reply.get("is_error") or reply.get("subtype") != "success":
            detail = str(reply.get("result") or reply.get("subtype") or "Claude Code reported an error")[:500]
            return AiResponse(data=None, usage=usage, model=used, latency_ms=latency, error=self._error_kind(detail),
                              error_detail=detail)
        data = reply.get("structured_output")
        if not isinstance(data, dict):
            return AiResponse(data=None, usage=usage, model=used, latency_ms=latency, error="invalid_response",
                              error_detail="the reply carried no structured output", raw_text=str(reply.get("result"))[:2000])
        return AiResponse(data=data, usage=usage, model=used, latency_ms=latency, raw_text=str(reply.get("result"))[:2000])


_provider: AiProvider | None = None
_provider_lock = threading.Lock()


def get_provider() -> AiProvider:
    """The configured provider, built once. `NullProvider` whenever AI is
    off, so callers never need to check the flag themselves."""
    global _provider
    with _provider_lock:
        if _provider is None:
            settings = get_settings()
            builders = {"claude-code": ClaudeCodeProvider, "claude_code": ClaudeCodeProvider,
                        "subscription": ClaudeCodeProvider,
                        "claude": ClaudeProvider, "anthropic": ClaudeProvider,
                        "openai": OpenAiProvider, "gpt": OpenAiProvider}
            build = builders.get(settings.ai_provider.lower()) if settings.ai_enabled else None
            if build is None:
                _provider = NullProvider()
            else:
                try:
                    _provider = build()
                except Exception:  # noqa: BLE001 -- a provider that cannot be built is a disabled one
                    _provider = NullProvider()
        return _provider


def set_provider(provider: AiProvider | None) -> None:
    """Swap the provider (tests, or a diagnostics switch)."""
    global _provider
    with _provider_lock:
        _provider = provider
