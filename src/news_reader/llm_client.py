"""Small, dependency-free client for the Gemini generateContent REST API."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[2]
LOCAL_ENV_PATH = PROJECT_ROOT / ".env.local"
LOCAL_ENV_KEYS = {"GEMINI_API_KEY", "GOOGLE_API_KEY", "GEMINI_MODEL"}


def load_local_env(path: Path = LOCAL_ENV_PATH) -> dict[str, str]:
    """Read supported settings from .env.local without changing os.environ."""
    if not path.is_file():
        return {}
    settings: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return {}
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.lower().startswith("export "):
            line = line[7:].strip()
        key, separator, value = line.partition("=")
        key = key.strip()
        if not separator or key not in LOCAL_ENV_KEYS:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        settings[key] = value
    return settings


class LlmError(RuntimeError):
    """Base error shown by the market assistant UI."""


class LlmConfigurationError(LlmError):
    """The cloud provider is not configured or rejected the credentials."""


class LlmRateLimitError(LlmError):
    """The free quota or provider rate limit was reached."""


@dataclass(frozen=True)
class ChatMessage:
    role: str
    text: str


class GeminiClient:
    """Call Gemini directly without adding a third-party SDK dependency."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        timeout: int = 45,
    ) -> None:
        local_env = load_local_env()
        self.api_key = (
            api_key
            or os.environ.get("GEMINI_API_KEY")
            or os.environ.get("GOOGLE_API_KEY")
            or local_env.get("GEMINI_API_KEY")
            or local_env.get("GOOGLE_API_KEY")
            or ""
        ).strip()
        self.model = (
            model
            or os.environ.get("GEMINI_MODEL")
            or local_env.get("GEMINI_MODEL")
            or "gemini-3.6-flash"
        ).strip()
        self.timeout = timeout

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key)

    @property
    def provider_label(self) -> str:
        if not self.is_configured:
            return "本機摘要（未設定 Gemini API Key）"
        return f"Google Gemini｜{self.model}"

    def generate(
        self,
        messages: Iterable[ChatMessage],
        *,
        system_instruction: str,
        max_output_tokens: int = 2048,
        use_google_search: bool = False,
        thinking_level: str = "low",
        max_continuations: int = 2,
    ) -> str:
        if not self.api_key:
            raise LlmConfigurationError(
                "尚未設定 GEMINI_API_KEY；目前仍可使用本機市場摘要。"
            )

        contents = []
        for message in messages:
            role = "model" if message.role == "assistant" else "user"
            contents.append({"role": role, "parts": [{"text": message.text}]})
        payload = {
            "systemInstruction": {"parts": [{"text": system_instruction}]},
            "contents": contents,
            "generationConfig": {
                "temperature": 0.25,
                "maxOutputTokens": max_output_tokens,
                "thinkingConfig": {"thinkingLevel": thinking_level},
            },
        }
        if use_google_search:
            payload["tools"] = [{"google_search": {}}]
        answer = ""
        sources: list[tuple[str, str]] = []
        still_truncated = False
        for attempt in range(max_continuations + 1):
            try:
                result = self._post(payload)
            except LlmError as exc:
                if answer:
                    return (
                        answer
                        + "\n\n[系統提示：自動接續時發生錯誤："
                        + str(exc)
                        + "]"
                    )
                raise
            text, response_sources, finish_reasons = self._extract_result(result)
            for source in response_sources:
                if source not in sources:
                    sources.append(source)
            if not text:
                if (
                    "MAX_TOKENS" in finish_reasons
                    and attempt < max_continuations
                ):
                    payload["generationConfig"]["thinkingConfig"] = {
                        "thinkingLevel": "minimal"
                    }
                    payload["generationConfig"]["maxOutputTokens"] = max(
                        max_output_tokens,
                        4096,
                    )
                    continue
                block_reason = result.get("promptFeedback", {}).get("blockReason")
                if block_reason:
                    raise LlmError(f"Gemini 未產生內容（{block_reason}）。")
                if answer:
                    break
                raise LlmError("Gemini 未回傳可顯示的文字。")
            answer = self._merge_continuation(answer, text)
            still_truncated = "MAX_TOKENS" in finish_reasons
            if not still_truncated:
                break
            if attempt >= max_continuations:
                break

            # Continue from the partial answer. Search is intentionally disabled
            # for continuation calls so one user question does not repeat searches.
            contents.extend(
                [
                    {"role": "model", "parts": [{"text": text}]},
                    {
                        "role": "user",
                        "parts": [
                            {
                                "text": (
                                    "上一段因輸出上限中斷。請從中斷處直接繼續，"
                                    "不要重複已完成的內容，並完成原本要求的結論。"
                                )
                            }
                        ],
                    },
                ]
            )
            payload["contents"] = contents
            payload.pop("tools", None)
            payload["generationConfig"]["thinkingConfig"] = {
                "thinkingLevel": "minimal"
            }

        if sources:
            answer += "\n\n新聞與網路來源\n" + "\n".join(
                f"[{index}] {title}\n{uri}"
                for index, (title, uri) in enumerate(sources[:10], start=1)
            )
        if still_truncated:
            answer += (
                "\n\n[系統提示：已自動接續兩次，但回答仍超過顯示上限；"
                "可縮小問題範圍後重試。]"
            )
        return answer

    def _post(self, payload: dict) -> dict:
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.model}:generateContent"
        )
        request = urllib.request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            method="POST",
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "x-goog-api-key": self.api_key,
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = self._error_detail(exc)
            if exc.code == 429:
                raise LlmRateLimitError(
                    f"Gemini 免費額度或速率限制已達上限（429）：{detail}"
                ) from exc
            if exc.code in (400, 401, 403, 404):
                raise LlmConfigurationError(
                    f"Gemini 設定或授權失敗（HTTP {exc.code}）：{detail}"
                ) from exc
            raise LlmError(f"Gemini 服務錯誤（HTTP {exc.code}）：{detail}") from exc
        except (OSError, TimeoutError) as exc:
            raise LlmError(f"無法連線 Gemini：{exc}") from exc
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise LlmError("Gemini 回應不是有效的 JSON。") from exc

    @staticmethod
    def _extract_result(
        result: dict,
    ) -> tuple[str, list[tuple[str, str]], set[str]]:
        texts = []
        sources: list[tuple[str, str]] = []
        finish_reasons: set[str] = set()
        for candidate in result.get("candidates", []):
            finish_reason = candidate.get("finishReason")
            if finish_reason:
                finish_reasons.add(str(finish_reason))
            for part in candidate.get("content", {}).get("parts", []):
                text = part.get("text")
                if text and not part.get("thought"):
                    texts.append(text.strip())
            for chunk in candidate.get("groundingMetadata", {}).get(
                "groundingChunks", []
            ):
                web = chunk.get("web", {})
                uri = str(web.get("uri") or "").strip()
                title = str(web.get("title") or uri).strip()
                if uri and (title, uri) not in sources:
                    sources.append((title, uri))
        return "\n".join(texts), sources, finish_reasons

    @staticmethod
    def _merge_continuation(existing: str, continuation: str) -> str:
        if not existing:
            return continuation.strip()
        left = existing.rstrip()
        right = continuation.lstrip()
        maximum = min(len(left), len(right), 800)
        for overlap in range(maximum, 7, -1):
            if left[-overlap:] == right[:overlap]:
                return left + right[overlap:]
        return left + "\n" + right

    @staticmethod
    def _error_detail(exc: urllib.error.HTTPError) -> str:
        try:
            payload = json.loads(exc.read().decode("utf-8"))
            return str(payload.get("error", {}).get("message") or exc.reason)
        except (json.JSONDecodeError, UnicodeDecodeError, OSError):
            return str(exc.reason)
