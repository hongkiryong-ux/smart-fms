"""ChatGPT 웹 — 브라우저 세션으로 backend-api SSE 스트리밍"""

from __future__ import annotations

import uuid
from typing import Callable, Optional

# chatgpt.com 페이지 컨텍스트에서 실행 — 웹 UI와 동일한 SSE 경로
STREAM_SCRIPT = """
async (prompt) => {
  let session = null;
  for (let i = 0; i < 25; i++) {
    session = await fetch('/api/auth/session').then(r => r.json());
    if (session.accessToken) break;
    await new Promise(r => setTimeout(r, 200));
  }
  if (!session?.accessToken) throw new Error('NOT_LOGGED_IN');

  const uid = () => crypto.randomUUID();
  const body = {
    action: 'next',
    messages: [{
      id: uid(),
      author: { role: 'user' },
      content: { content_type: 'text', parts: [prompt] },
      metadata: {}
    }],
    parent_message_id: uid(),
    model: 'auto',
    timezone_offset_min: new Date().getTimezoneOffset(),
    history_and_training_disabled: false,
    conversation_mode: { kind: 'primary_assistant' },
    force_paragen: false,
    force_rate_limit: false,
    reset_rate_limits: false,
  };

  const headers = {
    'Content-Type': 'application/json',
    'Authorization': 'Bearer ' + session.accessToken,
  };

  try {
    const req = await fetch('/backend-api/sentinel/chat-requirements', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': 'Bearer ' + session.accessToken,
      },
      body: JSON.stringify({ p: '' }),
    });
    if (req.ok) {
      const reqData = await req.json();
      if (reqData.token) headers['Openai-Sentinel-Chat-Requirements-Token'] = reqData.token;
      if (reqData.turnstile?.dx) headers['Openai-Sentinel-Turnstile-Token'] = reqData.turnstile.dx;
    }
  } catch (e) { /* optional */ }

  const resp = await fetch('/backend-api/conversation', {
    method: 'POST',
    headers,
    body: JSON.stringify(body),
  });

  if (!resp.ok) {
    const errText = await resp.text().catch(() => '');
    throw new Error('API_' + resp.status + ':' + errText.slice(0, 160));
  }

  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let full = '';
  let lastLen = 0;

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split('\\n');
    buffer = lines.pop() || '';

    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed.startsWith('data:')) continue;
      const payload = trimmed.slice(5).trim();
      if (payload === '[DONE]') continue;
      try {
        const data = JSON.parse(payload);
        const msg = data.message || data.v?.message;
        if (!msg?.content?.parts?.length) continue;
        const chunk = msg.content.parts.filter(p => typeof p === 'string').join('');
        if (chunk.length > lastLen) {
          const delta = chunk.slice(lastLen);
          lastLen = chunk.length;
          full = chunk;
          if (delta && window.__pwStreamDelta) await window.__pwStreamDelta(delta);
        }
      } catch (e) { /* skip */ }
    }
  }
  return full;
}
"""

HAS_TOKEN_SCRIPT = """
async () => {
  const s = await fetch('/api/auth/session').then(r => r.json());
  return !!s.accessToken;
}
"""

_stream_callback: Optional[Callable[[str], None]] = None


def set_stream_callback(cb: Optional[Callable[[str], None]]) -> None:
    global _stream_callback
    _stream_callback = cb


def global_stream_dispatch(text: str) -> None:
    if _stream_callback:
        _stream_callback(text)


def has_access_token(page) -> bool:
    try:
        return bool(page.evaluate(HAS_TOKEN_SCRIPT))
    except Exception:
        return False


def stream_chatgpt(
    page,
    prompt: str,
    timeout_ms: int = 120000,
) -> str:
    try:
        result = page.evaluate(STREAM_SCRIPT, prompt[:12000], timeout=timeout_ms)
        return (result or "").strip()
    except Exception as e:
        err = str(e)
        if "NOT_LOGGED_IN" in err:
            raise RuntimeError("ChatGPT 로그인이 필요합니다. AI 설정에서 다시 로그인하세요.") from e
        raise


def new_message_id() -> str:
    return str(uuid.uuid4())
