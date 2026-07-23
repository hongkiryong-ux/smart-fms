"""통합 AI 클라이언트 — ChatGPT / Gemini (API 키 또는 웹 로그인)"""

from __future__ import annotations

import os
from typing import Callable, Optional

from dotenv import load_dotenv

from app.ai_settings import AISettings, PROVIDERS
from app.ai_web_client import WebAIClient
from app.prompts import load_system_prompt, load_web_system_prompt

load_dotenv()


def _format_api_error(exc: Exception, provider: str) -> str:
    msg = str(exc).lower()
    label = "OpenAI" if provider == "chatgpt_api" else "Gemini" if provider == "gemini_api" else "AI"
    if "insufficient_quota" in msg or ("429" in msg and "quota" in msg):
        return (
            f"{label} API 사용 한도가 초과되었습니다.\n\n"
            "platform.openai.com (또는 Google AI Studio)에서\n"
            "결제·크레딧·요금제를 확인한 뒤 다시 시도해 주세요."
        )
    if "429" in msg or "rate limit" in msg:
        return f"{label} API 요청이 너무 많습니다. 잠시 후 다시 시도해 주세요."
    if "401" in msg or "invalid_api_key" in msg or "incorrect api key" in msg:
        return f"{label} API 키가 올바르지 않습니다.\n\n사이드바에서 API 키를 다시 저장해 주세요."
    if "timeout" in msg or "timed out" in msg:
        return f"{label} API 응답 시간이 초과되었습니다.\n\n네트워크 연결을 확인하고 다시 시도해 주세요."
    return str(exc)


class AIClient:
    def __init__(self):
        self.settings = AISettings.load()
        self._web: WebAIClient | None = None
        self._openai = None
        self._gemini_model = None
        self._web_login_cache: bool | None = None
        self._refresh_clients()

    def _refresh_clients(self) -> None:
        if self._web is not None:
            try:
                self._web.close()
            except Exception:
                pass
        self._web = None
        self._openai = None
        self._gemini_model = None

        provider = self.settings.provider
        if provider.endswith("_web"):
            self._web = WebAIClient(provider, self.settings.profile_path())
            return

        if provider == "chatgpt_api":
            try:
                from openai import OpenAI
            except ImportError:
                return
            key = self.settings.openai_api_key.strip()
            if key:
                self._openai = OpenAI(api_key=key, timeout=120.0, max_retries=2)
            return

        if provider == "gemini_api":
            key = self.settings.gemini_api_key.strip()
            if not key:
                return
            try:
                import google.generativeai as genai
            except ImportError:
                return
            genai.configure(api_key=key)
            self._gemini_model = genai.GenerativeModel(self.settings.gemini_model)

    def reload(self) -> None:
        self.settings = AISettings.load()
        self._refresh_clients()
        self._web_login_cache = None

    def apply_settings(self, settings: AISettings) -> None:
        old_provider = self.settings.provider
        self.settings = settings
        settings.save()
        self._web_login_cache = None
        if old_provider != settings.provider and settings.provider.endswith("_web"):
            settings.clear_web_login()
        self._refresh_clients()

    @property
    def provider(self) -> str:
        return self.settings.provider

    @property
    def is_configured(self) -> bool:
        p = self.settings.provider
        if p == "chatgpt_api":
            return self._openai is not None
        if p == "gemini_api":
            return self._gemini_model is not None
        if p.endswith("_web"):
            return self._web is not None
        return False

    def is_web_logged_in(self, verify: bool = False) -> bool:
        if not self.settings.provider.endswith("_web") or not self._web:
            return False
        if self.settings.web_session_valid() and not verify:
            return True
        if not verify and self._web.profile_has_session():
            return True
        if not verify:
            return False
        try:
            ok = self._web.is_logged_in()
            if ok:
                self.settings.mark_web_logged_in()
            else:
                self.settings.clear_web_login()
            return ok
        except Exception:
            return False

    def status_text(self) -> str:
        label = self.settings.provider_label()
        p = self.settings.provider
        if p == "chatgpt_api":
            return f"{label} — 연결됨 ({self.settings.openai_model})" if self.is_configured else f"{label} — API 키 필요"
        if p == "gemini_api":
            return f"{label} — 연결됨 ({self.settings.gemini_model})" if self.is_configured else f"{label} — API 키 필요"
        if p.endswith("_web"):
            if self.is_web_logged_in():
                return f"{label} — 로그인됨"
            return f"{label} — 로그인 필요"
        return label

    def login_web(self) -> tuple[bool, str]:
        if not self.settings.provider.endswith("_web"):
            return False, "웹 로그인은 ChatGPT/Gemini 웹 모드에서만 사용할 수 있습니다."
        self._web = WebAIClient(self.settings.provider, self.settings.profile_path())
        ok, msg = self._web.open_login_browser()
        if ok:
            self._web_login_cache = True
            self.settings.mark_web_logged_in()
        return ok, msg

    def generate(
        self,
        user_message: str,
        system_prompt: Optional[str] = None,
        on_chunk: Optional[Callable[[str], None]] = None,
        on_status: Optional[Callable[[str], None]] = None,
    ) -> str:
        p = self.settings.provider
        if p.endswith("_web"):
            system = system_prompt or load_web_system_prompt()
            # 웹: system+user 한 블록으로 전달 (중복 헤더 제거)
            user_message = f"{system}\n\n{user_message}" if system else user_message
        else:
            system = system_prompt or load_system_prompt()

        if p == "chatgpt_api":
            return self._generate_openai(user_message, system, on_chunk)
        if p == "gemini_api":
            return self._generate_gemini(user_message, system, on_chunk)
        if p.endswith("_web"):
            if not self._web:
                raise RuntimeError("웹 AI 클라이언트를 초기화할 수 없습니다.")
            if not self.is_web_logged_in(verify=False):
                raise RuntimeError(
                    "웹 로그인이 필요합니다. AI 설정에서 『로그인』 버튼을 눌러 ChatGPT/Gemini에 로그인하세요."
                )
            return self._web.generate(
                user_message, "", on_chunk=on_chunk, on_status=on_status
            )

        raise RuntimeError(f"지원하지 않는 AI 제공자: {p}")

    def generate_json(
        self,
        user_message: str,
        system_prompt: Optional[str] = None,
    ) -> str:
        """구조화 JSON 응답 (위험성평가 RiskRow[])."""
        p = self.settings.provider
        if p.endswith("_web"):
            system = system_prompt or load_web_system_prompt()
            user_message = f"{system}\n\n{user_message}" if system else user_message
        else:
            system = system_prompt or load_system_prompt()

        if p == "chatgpt_api":
            return self._generate_openai_json(user_message, system)
        if p == "gemini_api":
            return self._generate_gemini_json(user_message, system)
        if p.endswith("_web"):
            if not self._web:
                raise RuntimeError("웹 AI 클라이언트를 초기화할 수 없습니다.")
            if not self.is_web_logged_in(verify=False):
                raise RuntimeError(
                    "웹 로그인이 필요합니다. AI 설정에서 『로그인』 버튼을 눌러 ChatGPT/Gemini에 로그인하세요."
                )
            return self._web.generate(user_message, "", on_chunk=None, on_status=None)

        raise RuntimeError(f"지원하지 않는 AI 제공자: {p}")

    def _generate_openai_json(self, user_message: str, system: str) -> str:
        if not self._openai:
            raise RuntimeError(
                "OpenAI API 키가 설정되지 않았습니다.\n"
                "AI 설정에서 ChatGPT API 키를 입력하거나 .env에 OPENAI_API_KEY를 설정하세요."
            )
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user_message},
        ]
        model = self.settings.openai_model
        try:
            response = self._openai.chat.completions.create(
                model=model,
                messages=messages,
                temperature=0.2,
                max_tokens=16000,
                response_format={"type": "json_object"},
            )
            return response.choices[0].message.content or ""
        except Exception as e:
            raise RuntimeError(_format_api_error(e, "chatgpt_api")) from e

    def _generate_openai(
        self,
        user_message: str,
        system: str,
        on_chunk: Optional[Callable[[str], None]],
    ) -> str:
        if not self._openai:
            raise RuntimeError(
                "OpenAI API 키가 설정되지 않았습니다.\n"
                "AI 설정에서 ChatGPT API 키를 입력하거나 .env에 OPENAI_API_KEY를 설정하세요."
            )
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user_message},
        ]
        model = self.settings.openai_model

        try:
            if on_chunk:
                stream = self._openai.chat.completions.create(
                    model=model,
                    messages=messages,
                    temperature=0.3,
                    max_tokens=16000,
                    stream=True,
                )
                full: list[str] = []
                for chunk in stream:
                    delta = chunk.choices[0].delta.content or ""
                    if delta:
                        full.append(delta)
                        on_chunk(delta)
                return "".join(full)

            response = self._openai.chat.completions.create(
                model=model,
                messages=messages,
                temperature=0.3,
                max_tokens=16000,
            )
            return response.choices[0].message.content or ""
        except Exception as e:
            raise RuntimeError(_format_api_error(e, "chatgpt_api")) from e

    def _generate_gemini_json(self, user_message: str, system: str) -> str:
        if not self._gemini_model:
            raise RuntimeError(
                "Gemini API 키가 설정되지 않았습니다.\n"
                "AI 설정에서 Gemini API 키를 입력하거나 .env에 GEMINI_API_KEY를 설정하세요."
            )
        prompt = f"{system}\n\n---\n\n{user_message}"
        try:
            import google.generativeai as genai

            response = self._gemini_model.generate_content(
                prompt,
                generation_config=genai.GenerationConfig(
                    response_mime_type="application/json",
                    temperature=0.2,
                ),
            )
            return response.text or ""
        except Exception as e:
            raise RuntimeError(_format_api_error(e, "gemini_api")) from e

    def _generate_gemini(
        self,
        user_message: str,
        system: str,
        on_chunk: Optional[Callable[[str], None]],
    ) -> str:
        if not self._gemini_model:
            raise RuntimeError(
                "Gemini API 키가 설정되지 않았습니다.\n"
                "AI 설정에서 Gemini API 키를 입력하거나 .env에 GEMINI_API_KEY를 설정하세요."
            )
        prompt = f"{system}\n\n---\n\n{user_message}"

        try:
            if on_chunk:
                response = self._gemini_model.generate_content(prompt, stream=True)
                full: list[str] = []
                for chunk in response:
                    text = getattr(chunk, "text", "") or ""
                    if text:
                        full.append(text)
                        on_chunk(text)
                return "".join(full)

            response = self._gemini_model.generate_content(prompt)
            return response.text or ""
        except Exception as e:
            raise RuntimeError(_format_api_error(e, "gemini_api")) from e


# 하위 호환
class GPTClient(AIClient):
    pass
