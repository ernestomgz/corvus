from __future__ import annotations

import re
import html
from functools import lru_cache

import bleach
from markdown_it import MarkdownIt
from mdit_py_plugins.dollarmath import dollarmath_plugin


_MATH_BLOCK_PATTERN = re.compile(r"\[\$\$](?P<body>.+?)\[/\$\$]", re.DOTALL)
_HTML_SNIFFER = re.compile(r"<\s*([a-zA-Z!/?])")
MATH_SIGNAL_PATTERN = re.compile(r'(\\[a-zA-Z]+|\$\$|\$|\\\[|\\\]|\^|\\\(|\\\)|\\\{|\\\}|\\\/|\[latex|\[/latex])')
_WIKI_LINK_RE = re.compile(
    r'\[\[(?P<target>[^\]#|]+)'
    r'(?:#(?P<section>[^\]|]+))?'
    r'(?:\|(?P<label>[^\]]+))?'
    r'\]\]'
)

_ALLOWED_TAGS = sorted(
    {
        *bleach.sanitizer.ALLOWED_TAGS,
        'p', 'pre', 'code', 'span', 'div', 'img', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
        'ul', 'ol', 'li', 'blockquote', 'hr', 'table', 'thead', 'tbody', 'tr', 'th', 'td',
        'em', 'strong', 'a', 'br', 'sup', 'sub', 'figure', 'figcaption', 'audio', 'source',
        'script', 'svg', 'path', 'g', 'line', 'rect', 'circle', 'ellipse', 'polyline', 'polygon',
        'text', 'tspan', 'defs', 'marker', 'use', 'clipPath', 'symbol', 'title', 'desc', 'style',
    }
)

_ALLOWED_ATTRIBUTES = {
    **bleach.sanitizer.ALLOWED_ATTRIBUTES,
    'a': ['href', 'title', 'name', 'target', 'rel'],
    'img': ['src', 'alt', 'title', 'width', 'height'],
    'span': ['class'],
    'div': ['class'],
    'code': ['class'],
    'pre': ['class'],
    'audio': ['src', 'controls'],
    'source': ['src', 'type'],
    'script': ['type'],
    'svg': ['class', 'style', 'width', 'height', 'viewBox', 'xmlns', 'fill', 'stroke', 'stroke-width'],
    'g': ['class', 'style', 'fill', 'stroke', 'stroke-width', 'transform'],
    'path': ['class', 'style', 'd', 'fill', 'stroke', 'stroke-width', 'stroke-linecap', 'stroke-linejoin', 'marker-start', 'marker-end'],
    'line': ['class', 'style', 'x1', 'y1', 'x2', 'y2', 'stroke', 'stroke-width'],
    'rect': ['class', 'style', 'x', 'y', 'width', 'height', 'rx', 'ry', 'fill', 'stroke', 'stroke-width'],
    'circle': ['class', 'style', 'cx', 'cy', 'r', 'fill', 'stroke', 'stroke-width'],
    'ellipse': ['class', 'style', 'cx', 'cy', 'rx', 'ry', 'fill', 'stroke', 'stroke-width'],
    'polyline': ['class', 'style', 'points', 'fill', 'stroke', 'stroke-width'],
    'polygon': ['class', 'style', 'points', 'fill', 'stroke', 'stroke-width'],
    'text': ['class', 'style', 'x', 'y', 'fill', 'stroke', 'stroke-width', 'font-size', 'text-anchor'],
    'tspan': ['class', 'style', 'x', 'y', 'dx', 'dy'],
    'marker': ['id', 'orient', 'markerWidth', 'markerHeight', 'refX', 'refY', 'viewBox'],
    'use': ['href', 'x', 'y', 'width', 'height', 'transform'],
    'clipPath': ['id'],
    'style': ['type'],
}

_ALLOWED_PROTOCOLS = ['http', 'https', 'data']

_MATH_PLUGIN_CANDIDATES: tuple[dict[str, bool], ...] = (
    {'allow_inline': True, 'allow_display': True},
    {'allow_inline': True, 'allow_block': True},
    {'allow_block': True},
    {},
)
_MATH_ELEMENT_RE = re.compile(
    r'(<(?P<tag>span|div)\b(?P<before>[^>]*)class="(?P<class>[^"]*\bmath\b[^"]*)"(?P<after>[^>]*)>)(?P<body>.*?)(</(?P=tag)>)',
    re.DOTALL,
)
_INLINE_DELIMS = (('\\(', '\\)'), ('$', '$'))
_DISPLAY_DELIMS = (('$$', '$$'), ('\\[', '\\]'))
_INLINE_DOLLAR_PATTERN = re.compile(r'(?<!\\)\$(?!\$)(.+?)(?<!\\)\$', re.DOTALL)
_INLINE_PAREN_PATTERN = re.compile(r'\\\((.+?)\\\)', re.DOTALL)
_DISPLAY_DOLLAR_PATTERN = re.compile(r'(?<!\\)\$\$(.+?)(?<!\\)\$\$', re.DOTALL)
_DISPLAY_BRACKET_PATTERN = re.compile(r'\\\[(.+?)\\\]', re.DOTALL)
_MATH_PLACEHOLDER_TEMPLATE = '::MATHSEG_{index}::'
_MATH_PLACEHOLDER_RE = re.compile(r'::MATHSEG_(\d+)::')
_BLOCK_PLACEHOLDER_PARA_RE = re.compile(
    r'<p>\s*(?P<placeholder>::MATHSEG_(?P<idx>\d+)::)\s*</p>',
    re.IGNORECASE,
)
_MATH_CLASS_RE = re.compile(r'class=["\'][^"\']*\bmath\b', re.IGNORECASE)
_SCRIPT_TAG_RE = re.compile(r'<script(?P<attrs>[^>]*)>(?P<body>.*?)</script>', re.IGNORECASE | re.DOTALL)
_TIKZ_CODE_RE = re.compile(
    r'<pre><code class="(?P<class>[^"]*\blanguage-(?P<lang>tikz|latex)[^"]*)">(?P<body>.*?)</code></pre>',
    re.IGNORECASE | re.DOTALL,
)


class _MathSegment(tuple):
    __slots__ = ()

    def __new__(cls, kind: str, body: str):
        return super().__new__(cls, (kind, body))

    @property
    def kind(self) -> str:
        return self[0]

    @property
    def body(self) -> str:
        return self[1]


def _new_markdown_instance() -> MarkdownIt:
    md = MarkdownIt('commonmark', {'html': True, 'linkify': True, 'breaks': True})
    md.enable('table')
    md.enable('strikethrough')
    return md


def _build_markdown() -> MarkdownIt:
    for options in _MATH_PLUGIN_CANDIDATES:
        md = _new_markdown_instance()
        try:
            md.use(dollarmath_plugin, **options)
        except TypeError:
            continue
        return md
    md = _new_markdown_instance()
    md.use(dollarmath_plugin)
    return md


_markdown = _build_markdown()


def _contains_math_markup(value: str) -> bool:
    return bool(MATH_SIGNAL_PATTERN.search(value))


def _normalise_legacy_math(text: str) -> str:
    def to_display(body: str) -> str:
        body = body.strip()
        return f"$$\n{body}\n$$"

    def to_inline(body: str) -> str:
        return f"${body.strip()}$"

    value = _MATH_BLOCK_PATTERN.sub(lambda m: to_display(m.group('body')), text)
    value = re.sub(
        r'\[latex\](.+?)\[/latex\]',
        lambda m: to_display(m.group(1)),
        value,
        flags=re.IGNORECASE | re.DOTALL,
    )
    value = re.sub(
        r'\[\](.+?)\[/\]',
        lambda m: to_display(m.group(1)),
        value,
        flags=re.DOTALL,
    )
    value = re.sub(
        r'\[latex-inline\](.+?)\[/latex-inline\]',
        lambda m: to_inline(m.group(1)),
        value,
        flags=re.IGNORECASE | re.DOTALL,
    )
    return value


def _normalise_wiki_links(text: str) -> str:
    def repl(match: re.Match[str]) -> str:
        target = (match.group('target') or '').strip()
        section = (match.group('section') or '').strip()
        label = (match.group('label') or '').strip()
        # Ignore numeric size hints (e.g., [[graph.jpg|200]]) and prefer the target name.
        if label and label.isdigit():
            label = ''
        display = label or section or target
        return display

    return _WIKI_LINK_RE.sub(repl, text)


#def _looks_like_html(value: str) -> bool:
#    return bool(_HTML_SNIFFER.search(value))


def _normalise_classes(existing: list[str], required: list[str]) -> list[str]:
    seen: set[str] = set()
    merged: list[str] = []
    for item in existing + required:
        if not item:
            continue
        if item not in seen:
            merged.append(item)
            seen.add(item)
    return merged


def _strip_unsafe_scripts(html_value: str) -> str:
    def repl(match: re.Match[str]) -> str:
        attrs = (match.group('attrs') or '').lower()
        if 'text/tikz' in attrs:
            return match.group(0)
        return ''

    return _SCRIPT_TAG_RE.sub(repl, html_value)


def _promote_tikz_blocks(html_value: str) -> str:
    def repl(match: re.Match[str]) -> str:
        lang = (match.group('lang') or '').lower()
        raw_body = match.group('body') or ''
        body_text = html.unescape(raw_body)
        if '\\begin{tikzpicture' not in body_text and '\\begin{circuitikz' not in body_text:
            if lang != 'tikz':
                return match.group(0)
        return f'<script type="text/tikz">\n{body_text}\n</script>'

    return _TIKZ_CODE_RE.sub(repl, html_value)


def _has_delimiters(text: str, candidates: tuple[tuple[str, str], ...]) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    return any(stripped.startswith(start) and stripped.endswith(end) for start, end in candidates)


def _wrap_with_delimiters(body: str, start: str, end: str) -> str:
    leading_len = len(body) - len(body.lstrip())
    trailing_len = len(body) - len(body.rstrip())
    if leading_len:
        leading = body[:leading_len]
    else:
        leading = ''
    if trailing_len:
        trailing = body[-trailing_len:]
    else:
        trailing = ''
    core = body[leading_len: len(body) - trailing_len if trailing_len else len(body)]
    if not core:
        return body
    wrapped = f'{start}{core}{end}'
    return f'{leading}{wrapped}{trailing}'


def _ensure_mathjax_markup(html: str) -> str:
    def repl(match: re.Match[str]) -> str:
        tag = match.group('tag')
        before = match.group('before') or ''
        class_str = match.group('class')
        after = match.group('after') or ''
        body = match.group('body')
        if body is None:
            return match.group(0)
        classes = class_str.split()
        is_block = tag == 'div' or any(cls in {'block', 'math-block', 'math-display'} for cls in classes)
        candidates = _DISPLAY_DELIMS if is_block else _INLINE_DELIMS
        required_classes = ['math-block' if is_block else 'math-inline', 'math']
        classes = _normalise_classes(classes, required_classes)
        new_class_str = ' '.join(classes)
        if not _has_delimiters(body, candidates):
            start, end = candidates[0]
            body = _wrap_with_delimiters(body, start, end)
        return f'<{tag}{before}class="{new_class_str}"{after}>{body}</{tag}>'

    return _MATH_ELEMENT_RE.sub(repl, html)


def _make_placeholder(index: int) -> str:
    return _MATH_PLACEHOLDER_TEMPLATE.format(index=index)


def _clean_math_body(body: str) -> str:
    return body.strip()


def _extract_math_segments(value: str) -> tuple[str, list[_MathSegment]]:
    segments: list[_MathSegment] = []

    def store(kind: str, body: str) -> str:
        placeholder = _make_placeholder(len(segments))
        cleaned_body = _clean_math_body(body)
        segments.append(_MathSegment(kind, cleaned_body))
        return placeholder

    working = value

    for pattern in (_DISPLAY_DOLLAR_PATTERN, _DISPLAY_BRACKET_PATTERN):
        working = pattern.sub(lambda m: store('block', m.group(1)), working)

    working = _INLINE_PAREN_PATTERN.sub(lambda m: store('inline', m.group(1)), working)
    working = _INLINE_DOLLAR_PATTERN.sub(lambda m: store('inline', m.group(1)), working)

    return working, segments


def _render_math_segment(segment: _MathSegment, prefer_block: bool = True) -> str:
    body = segment.body
    if not body:
        return ''
    if segment.kind == 'block' and prefer_block:
        return f'<div class="math math-block">\\[{body}\\]</div>'
    if segment.kind == 'block':
        return f'<span class="math math-block">\\[{body}\\]</span>'
    return f'<span class="math math-inline">\\({body}\\)</span>'


def _restore_block_paragraphs(html: str, segments: list[_MathSegment]) -> str:
    def repl(match: re.Match[str]) -> str:
        idx = int(match.group('idx'))
        segment = segments[idx]
        if segment.kind != 'block':
            return match.group(0)
        return _render_math_segment(segment, prefer_block=True)

    return _BLOCK_PLACEHOLDER_PARA_RE.sub(repl, html)


def _restore_placeholders(html: str, segments: list[_MathSegment]) -> str:
    def repl(match: re.Match[str]) -> str:
        idx = int(match.group(1))
        if idx >= len(segments):
            return match.group(0)
        segment = segments[idx]
        return _render_math_segment(segment, prefer_block=False)

    return _MATH_PLACEHOLDER_RE.sub(repl, html)


@lru_cache(maxsize=256)
def _render_markdown_cached(raw: str) -> str:
    prepared = _normalise_legacy_math(raw)
    prepared = _normalise_wiki_links(prepared)
    placeholder_text, segments = _extract_math_segments(prepared)
    rendered = _markdown.render(placeholder_text)
    if segments:
        rendered = _restore_block_paragraphs(rendered, segments)
        rendered = _restore_placeholders(rendered, segments)
    return rendered


def _wrap_raw_math(html: str) -> str:
    if _MATH_CLASS_RE.search(html) is not None:
        return html
    if not MATH_SIGNAL_PATTERN.search(html):
        return html
    placeholder_text, segments = _extract_math_segments(html)
    if not segments:
        return html
    restored = _restore_block_paragraphs(placeholder_text, segments)
    restored = _restore_placeholders(restored, segments)
    return restored


def _has_known_delimiters(value: str) -> bool:
    return any(delim in value for delim in ('\\[', '\\]', '\\(', '\\)', '$$'))


def render_to_html(content: str | None) -> str:
    if not content:
        return ''

    # 1. ALWAYS render through Markdown first. 
    # This identifies ```bash blocks correctly and escapes the < and > symbols.
    html_value = _render_markdown_cached(content)

    # 2. Promote specific blocks (like TikZ) that were safely parsed as code
    html_value = _promote_tikz_blocks(html_value)

    # 3. Sanitize the HTML. 
    # Bleach will now see the shell redirect as "&lt;", which is safe.
    cleaned = bleach.clean(
        html_value,
        tags=_ALLOWED_TAGS,
        attributes=_ALLOWED_ATTRIBUTES,
        protocols=_ALLOWED_PROTOCOLS,
        strip=True,
    )

    # 4. Only ensure MathJax markup for things the parser ALREADY identified as math.
    cleaned = _ensure_mathjax_markup(cleaned)
    
    return cleaned
