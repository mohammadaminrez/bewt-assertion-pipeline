from __future__ import annotations

"""Deterministic HTML cleanup for the treatment C/D page context.

The whole page is kept (no length cut), minus content that carries no
information about the rendered state: <head>, scripts, styles, SVG, comments,
inline style/event-handler attributes, and whitespace runs. Element
structure, ids, classes, names, hrefs and form values are kept, so both the
visible text and the locators remain available to the model.
"""

import re
from html import escape
from html.parser import HTMLParser

_DROP = {"head", "script", "style", "noscript", "svg", "template", "link", "meta"}
_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
         "source", "track", "wbr"}


class _Cleaner(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.out: list[str] = []
        self.skip = 0  # depth inside a dropped element

    def _open(self, tag, attrs) -> str:
        kept = "".join(
            f' {k}="{escape(v, quote=True)}"' if v is not None else f" {k}"
            for k, v in attrs
            if k != "style" and not k.startswith("on")
        )
        return f"<{tag}{kept}>"

    def handle_starttag(self, tag, attrs):
        if self.skip or tag in _DROP:
            if tag not in _VOID:
                self.skip += 1
            return
        self.out.append(self._open(tag, attrs))

    def handle_startendtag(self, tag, attrs):
        if not self.skip and tag not in _DROP:
            self.out.append(self._open(tag, attrs))

    def handle_endtag(self, tag):
        if self.skip:
            if tag not in _VOID:
                self.skip -= 1
            return
        if tag not in _VOID:
            self.out.append(f"</{tag}>")

    def handle_data(self, data):
        if not self.skip and data.strip():
            self.out.append(re.sub(r"\s+", " ", data.strip()))

    def handle_entityref(self, name):
        if not self.skip:
            self.out.append(f"&{name};")

    def handle_charref(self, name):
        if not self.skip:
            self.out.append(f"&#{name};")


def clean_html(html: str) -> str:
    cleaner = _Cleaner()
    cleaner.feed(html)
    cleaner.close()
    return "\n".join(cleaner.out)
