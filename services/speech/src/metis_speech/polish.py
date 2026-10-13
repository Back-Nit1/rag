"""Deterministic dictation cleanup ("Flow" polish).

Runs on finalized ASR text only. It may delete fillers, fix case and terminal
punctuation, apply the user's own dictionary and snippets, and honour two
explicit spoken commands. It never rewrites content: dates, names and negation
are untouched, and there is no model call. Spoken self-correction ("no, wait,
Friday") is deliberately not guessed at; the raw text is kept for that.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from metis_contracts import FlowSettings

Style = Literal["standard", "casual", "literal"]

#: lowercase substring of the app name/id -> style, checked after the user's own map.
DEFAULT_APP_STYLES: dict[str, Style] = {
    "slack": "casual", "discord": "casual", "messages": "casual", "whatsapp": "casual",
    "telegram": "casual", "signal": "casual",
    "terminal": "literal", "iterm": "literal", "warp": "literal", "code": "literal",
    "cursor": "literal", "xcode": "literal", "intellij": "literal", "vim": "literal",
}  # fmt: skip

_FILLERS = re.compile(r"\b(?:u+m+|u+h+|e+r+m*|h+m+)\b[,.]?\s*", re.IGNORECASE)
_SCRATCH = re.compile(r"[,.\s]*\b(?:scratch that|delete that)\b[.,!?\s]*", re.IGNORECASE)
_PARAGRAPH = re.compile(r"[,.\s]*\bnew paragraph\b[.,]?\s*", re.IGNORECASE)
_LINE = re.compile(r"[,.\s]*\bnew line\b[.,]?\s*", re.IGNORECASE)
_SENTENCE_START = re.compile(r"(^|[.!?]\s+|\n)([a-z])(\w*)")
_LONE_I = re.compile(r"\bi\b(?=$|[\s',.!?])")


@dataclass(frozen=True)
class Polished:
    text: str
    style: Style
    applied: list[str] = field(default_factory=list)


def style_for(app: str | None, cfg: FlowSettings) -> Style:
    if not app:
        return "standard"
    name = app.lower()
    for table in (cfg.app_styles, DEFAULT_APP_STYLES):
        for key, style in table.items():
            if key.lower() in name:
                return style
    return "standard"


def _scratch(text: str) -> str:
    """Drop each 'scratch that' and the sentence spoken just before it."""
    while (m := _SCRATCH.search(text)) is not None:
        head = text[: m.start()]
        cut = max((head.rfind(c) for c in ".!?\n"), default=-1)
        text = (head[: cut + 1].rstrip() + " " + text[m.end() :].lstrip()).strip()
    return text


def _dictionary(text: str, cfg: FlowSettings) -> str:
    for entry in cfg.dictionary:
        for form in {*entry.aliases, entry.term}:
            text = re.sub(
                rf"(?<!\w){re.escape(form)}(?!\w)", entry.term.replace("\\", "\\\\"), text, flags=re.I
            )
    return text


def _snippets(text: str, cfg: FlowSettings) -> str:
    for snip in cfg.snippets:
        words = [re.escape(w) for w in snip.trigger.split()]
        pattern = r"(?<!\w)" + r"[\s,.]+".join(words) + r"(?!\w)[.,!?]*"
        text = re.sub(pattern, snip.expansion.replace("\\", "\\\\"), text, flags=re.I)
    return text


def polish(text: str, cfg: FlowSettings, *, app: str | None = None) -> Polished:
    style = style_for(app, cfg)
    applied: list[str] = []
    out = " ".join(text.split())
    if not out:
        return Polished("", style)

    def step(name: str, new: str) -> None:
        nonlocal out
        if new != out:
            applied.append(name)
            out = new

    if cfg.remove_fillers:
        step("fillers", _FILLERS.sub("", out).strip())
    step("scratch_that", _scratch(out))
    step("new_paragraph", _PARAGRAPH.sub("\n\n", out))
    step("new_line", _LINE.sub("\n", out))
    step("dictionary", _dictionary(out, cfg))

    if style != "literal":
        protected = {e.term for e in cfg.dictionary}

        def cap(m: re.Match[str]) -> str:
            word = m.group(2) + m.group(3)
            return m.group(0) if word in protected else m.group(1) + m.group(2).upper() + m.group(3)

        step("case", _LONE_I.sub("I", _SENTENCE_START.sub(cap, out)))
        if style == "casual":
            step("trailing_period", re.sub(r"(?<![.!?])\.$", "", out))
        elif out and out[-1].isalnum():
            step("trailing_period", out + ".")

    step("snippets", _snippets(out, cfg))
    return Polished(out.strip(), style, applied)
