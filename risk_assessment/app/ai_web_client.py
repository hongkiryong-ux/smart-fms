"""ChatGPT / Gemini 웹 — Chrome 프로필 + DOM 작성 (안정 우선)"""

from __future__ import annotations

import re
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Generator, Optional

from app.chatgpt_stream import has_access_token, set_stream_callback, stream_chatgpt

LOGIN_URLS = {
    "chatgpt_web": "https://chatgpt.com/",
    "gemini_web": "https://gemini.google.com/app",
}

CHROME_CHANNEL = "chrome"
CHROME_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--disable-background-networking",
    "--disable-sync",
    "--no-first-run",
    "--disable-extensions",
]


def _playwright_available() -> bool:
    try:
        import playwright  # noqa: F401
        return True
    except ImportError:
        return False


class WebAIClient:
    """브라우저 프로필 기반 ChatGPT/Gemini 웹 작성"""

    def __init__(self, provider: str, profile_dir: Path):
        if provider not in LOGIN_URLS:
            raise ValueError(f"지원하지 않는 웹 제공자: {provider}")
        self.provider = provider
        self.profile_dir = profile_dir
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    @staticmethod
    def install_hint() -> str:
        return (
            "웹 로그인에는 Google Chrome과 Playwright가 필요합니다.\n"
            "  pip install playwright\n"
            "  PC에 Google Chrome 설치"
        )

    def _launch_context(self, playwright, *, for_login: bool = False):
        args = list(CHROME_ARGS)
        if not for_login:
            args.extend(["--window-size=1280,900", "--window-position=-32000,-32000"])
        try:
            return playwright.chromium.launch_persistent_context(
                user_data_dir=str(self.profile_dir),
                channel=CHROME_CHANNEL,
                headless=False,
                locale="ko-KR",
                args=args,
            )
        except Exception as e:
            msg = str(e).lower()
            if "chrome" in msg or "channel" in msg or "executable" in msg or "in use" in msg:
                raise RuntimeError(
                    "Chrome을 실행할 수 없습니다.\n"
                    "다른 Chrome 창(로그인용)을 닫고 다시 시도하거나,\n"
                    "ChatGPT/Gemini (API 키) 모드를 사용하세요."
                ) from e
            raise

    @contextmanager
    def _page_session(
        self,
        *,
        for_login: bool = False,
        visible: bool = False,
        on_status: Optional[Callable[[str], None]] = None,
    ) -> Generator:
        if not _playwright_available():
            raise RuntimeError(self.install_hint())

        from playwright.sync_api import sync_playwright

        url = LOGIN_URLS[self.provider]
        if on_status:
            on_status("Chrome 시작…" if for_login else "브라우저 접속…")

        with sync_playwright() as p:
            ctx = self._launch_context(p, for_login=(for_login or visible))
            try:
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
                if for_login or visible:
                    page.bring_to_front()
                else:
                    page.wait_for_timeout(1500)
                yield page
            finally:
                try:
                    ctx.close()
                except Exception:
                    pass

    def _is_logged_in_page(self, page) -> bool:
        if self.provider == "chatgpt_web":
            return bool(
                page.evaluate(
                    """async () => {
                      try {
                        const s = await fetch('/api/auth/session').then(r => r.json());
                        if (s.accessToken) return true;
                      } catch (e) {}
                      const profile = document.querySelector(
                        '[data-testid="profile-button"], button[aria-label*="Profile"], [aria-label*="프로필"]'
                      );
                      const login = document.querySelector(
                        'button[data-testid="login-button"], a[href*="login"]'
                      );
                      const ta = document.querySelector('#prompt-textarea, textarea');
                      return !!ta && !!profile && !login;
                    }"""
                )
            )
        return self._detect_ready(page)

    def profile_has_session(self) -> bool:
        markers = (
            self.profile_dir / "Default" / "Network" / "Network Persistent State",
            self.profile_dir / "Default" / "Local Storage" / "leveldb",
            self.profile_dir / "Local State",
        )
        for p in markers:
            if p.exists() and (p.stat().st_size > 256 if p.is_file() else any(p.iterdir())):
                return True
        return False

    def close(self) -> None:
        pass  # 매 작업마다 브라우저 종료

    def _detect_ready(self, page) -> bool:
        if self.provider == "chatgpt_web":
            selectors = (
                "#prompt-textarea",
                '[data-testid="prompt-textarea"]',
                'textarea[placeholder*="Message"]',
                'textarea[placeholder*="메시지"]',
                "textarea",
            )
        else:
            selectors = (
                "rich-textarea",
                ".ql-editor",
                'div[contenteditable="true"]',
                '[aria-label*="Enter a prompt"]',
                '[aria-label*="프롬프트"]',
            )
        for sel in selectors:
            try:
                if page.locator(sel).first.is_visible(timeout=2000):
                    return True
            except Exception:
                continue
        return False

    def open_login_browser(self, wait_timeout: float = 300.0) -> tuple[bool, str]:
        with self._lock:
            try:
                with self._page_session(for_login=True) as page:
                    deadline = time.time() + wait_timeout
                    while time.time() < deadline:
                        if self._is_logged_in_page(page):
                            return True, "로그인 완료 — 세션이 저장되었습니다."
                        time.sleep(0.8)
                    return False, (
                        "로그인 시간이 초과되었습니다.\n"
                        "ChatGPT/Gemini 계정으로 로그인한 뒤 다시 시도하세요."
                    )
            except Exception as e:
                return False, str(e)

    def is_logged_in(self) -> bool:
        with self._lock:
            try:
                with self._page_session() as page:
                    return self._is_logged_in_page(page)
            except Exception:
                return False

    def generate(
        self,
        user_message: str,
        system_prompt: str = "",
        on_chunk: Optional[Callable[[str], None]] = None,
        on_status: Optional[Callable[[str], None]] = None,
        timeout: float = 180.0,
    ) -> str:
        prompt = user_message
        if system_prompt:
            prompt = f"{system_prompt}\n\n---\n\n{user_message}"

        with self._lock:
            visible = self.provider == "chatgpt_web"
            with self._page_session(visible=visible, on_status=on_status) as page:
                if not self._is_logged_in_page(page):
                    name = "ChatGPT" if self.provider == "chatgpt_web" else "Gemini"
                    raise RuntimeError(
                        f"{name} 로그인이 필요합니다.\n"
                        "AI 설정 → 『로그인 (브라우저 열기)』에서 Google/ChatGPT 계정으로 로그인하세요."
                    )

                if on_status:
                    label = "ChatGPT" if self.provider == "chatgpt_web" else "Gemini"
                    on_status(f"{label} 작성 중…")

                if self.provider == "chatgpt_web":
                    text = self._ask_chatgpt(page, prompt, on_chunk, timeout, on_status)
                else:
                    text = self._ask_gemini(page, prompt, on_chunk, timeout)

                if not text.strip():
                    name = "ChatGPT" if self.provider == "chatgpt_web" else "Gemini"
                    raise RuntimeError(
                        f"{name} 응답이 없습니다.\n"
                        "계정 로그인 상태를 확인하거나 API 키 모드를 사용하세요."
                    )
                return text

    def _ask_chatgpt(
        self,
        page,
        prompt: str,
        on_chunk: Optional[Callable[[str], None]],
        timeout: float,
        on_status: Optional[Callable[[str], None]] = None,
    ) -> str:
        # accessToken 있으면 SSE(빠름), 없으면 DOM(로그인 UI와 동일)
        if has_access_token(page):
            try:
                set_stream_callback(on_chunk)
                try:
                    page.expose_function("__pwStreamDelta", lambda t: on_chunk(t) if on_chunk else None)
                except Exception:
                    pass
                if on_status:
                    on_status("ChatGPT 스트리밍…")
                return stream_chatgpt(page, prompt, timeout_ms=int(timeout * 1000))
            except Exception:
                if on_status:
                    on_status("ChatGPT 화면 작성…")

        return self._ask_chatgpt_dom(page, prompt, on_chunk, timeout)

    def _ask_chatgpt_dom(
        self,
        page,
        prompt: str,
        on_chunk: Optional[Callable[[str], None]],
        timeout: float,
    ) -> str:
        self._click_new_chat(page)
        ok = page.evaluate(
            """(text) => {
              const sel = '#prompt-textarea, [data-testid="prompt-textarea"], textarea, [contenteditable="true"]';
              const el = document.querySelector(sel);
              if (!el) return false;
              el.focus();
              if (el.tagName === 'TEXTAREA' || el.tagName === 'INPUT') {
                el.value = text;
                el.dispatchEvent(new Event('input', { bubbles: true }));
              } else {
                el.textContent = text;
                el.dispatchEvent(new InputEvent('input', { bubbles: true, data: text }));
              }
              return true;
            }""",
            prompt[:12000],
        )
        if not ok:
            textarea = page.locator("#prompt-textarea, [data-testid='prompt-textarea'], textarea").first
            textarea.click(force=True, timeout=8000)
            textarea.fill(prompt[:12000], force=True)

        page.keyboard.press("Enter")
        return self._poll_chatgpt(page, on_chunk, timeout)

    def _ask_gemini(
        self,
        page,
        prompt: str,
        on_chunk: Optional[Callable[[str], None]],
        timeout: float,
    ) -> str:
        self._click_new_chat(page)
        for sel in (
            "rich-textarea div[contenteditable='true']",
            ".ql-editor",
            "rich-textarea",
            'div[contenteditable="true"]',
        ):
            try:
                editor = page.locator(sel).first
                if editor.is_visible(timeout=3000):
                    editor.click(timeout=5000)
                    editor.fill(prompt[:12000])
                    editor.press("Enter")
                    text = self._poll_gemini(page, on_chunk, timeout)
                    return re.sub(r"\n{3,}", "\n\n", text).strip()
            except Exception:
                continue
        raise RuntimeError("Gemini 입력창을 찾을 수 없습니다. gemini.google.com에 로그인했는지 확인하세요.")

    def _click_new_chat(self, page) -> None:
        if self.provider == "chatgpt_web":
            selectors = (
                '[data-testid="create-new-chat-button"]',
                'button[aria-label*="New chat"]',
                'button[aria-label*="새 채팅"]',
            )
        else:
            selectors = (
                'button[aria-label*="New chat"]',
                '[data-test-id="new-chat-button"]',
                'a[aria-label*="New chat"]',
            )
        for sel in selectors:
            try:
                btn = page.locator(sel).first
                if btn.is_visible(timeout=1200):
                    btn.click(timeout=3000)
                    page.wait_for_timeout(400)
                    return
            except Exception:
                continue

    def _poll_chatgpt(self, page, on_chunk, timeout: float) -> str:
        script = """
        () => {
          const nodes = document.querySelectorAll('[data-message-author-role="assistant"]');
          if (!nodes.length) return '';
          return nodes[nodes.length - 1].innerText || '';
        }
        """
        return self._poll_text(page, script, on_chunk, timeout)

    def _poll_gemini(self, page, on_chunk, timeout: float) -> str:
        script = """
        () => {
          const sels = [
            'message-content.model-response-text',
            '.model-response-text',
            'message-content',
            '.response-content',
            '[data-test-id="model-response"]',
            '.markdown',
          ];
          for (const sel of sels) {
            const nodes = document.querySelectorAll(sel);
            if (nodes.length) return nodes[nodes.length - 1].innerText || '';
          }
          return '';
        }
        """
        return self._poll_text(page, script, on_chunk, timeout)

    def _poll_text(self, page, script: str, on_chunk, timeout: float) -> str:
        deadline = time.time() + timeout
        last_text = ""
        stable = 0
        while time.time() < deadline:
            try:
                text = page.evaluate(script) or ""
            except Exception:
                text = ""
            if text and text != last_text:
                if on_chunk and len(text) > len(last_text):
                    on_chunk(text[len(last_text):])
                last_text = text
                stable = 0
            else:
                stable += 1
                if stable >= 3 and last_text:
                    break
            time.sleep(0.25)
        return last_text.strip()
