"""출력 Textbox — URL 하이퍼링크 삽입·스크롤"""

from __future__ import annotations

import re
import webbrowser

import customtkinter as ctk

URL_RE = re.compile(r"https?://[^\s\)\]>\"']+")

_link_counter = 0


def _inner(textbox: ctk.CTkTextbox):
    return textbox._textbox


def configure_link_widget(textbox: ctk.CTkTextbox) -> None:
    if getattr(textbox, "_links_configured", False):
        return
    textbox._links_configured = True


def _register_link_tag(tb, url: str) -> str:
    global _link_counter
    _link_counter += 1
    tag = f"pwlink_{_link_counter}"
    clean = url.rstrip(".,;)")
    tb.tag_configure(tag, foreground="#2563eb", underline=True)
    tb.tag_bind(tag, "<Button-1>", lambda _e, u=clean: webbrowser.open(u))
    tb.tag_bind(tag, "<Enter>", lambda _e: tb.configure(cursor="hand2"))
    tb.tag_bind(tag, "<Leave>", lambda _e: tb.configure(cursor=""))
    return tag


def insert_with_links(textbox: ctk.CTkTextbox, text: str, *, clear: bool = False) -> None:
    """텍스트 삽입 — http(s) URL 을 클릭 가능 링크로 표시"""
    configure_link_widget(textbox)
    tb = _inner(textbox)
    if clear:
        tb.delete("1.0", "end")

    pos = 0
    for m in URL_RE.finditer(text):
        before = text[pos : m.start()]
        if before:
            tb.insert("end", before)
        url = m.group(0).rstrip(".,;)")
        tag = _register_link_tag(tb, url)
        tb.insert("end", url, (tag,))
        pos = m.end()
    rest = text[pos:]
    if rest:
        tb.insert("end", rest)


def scroll_to_top(textbox: ctk.CTkTextbox) -> None:
    tb = _inner(textbox)
    tb.see("1.0")
    tb.yview_moveto(0.0)
    tb.mark_set("insert", "1.0")


def scroll_to_bottom(textbox: ctk.CTkTextbox) -> None:
    tb = _inner(textbox)
    tb.see("end")
    tb.mark_set("insert", "end")
