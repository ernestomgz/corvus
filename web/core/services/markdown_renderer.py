from __future__ import annotations

import re
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
        'svg', 'path', 'g', 'line', 'rect', 'circle', 'ellipse', 'polyline', 'polygon',
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
_TIKZ_CODE_RE = re.compile(
    r'<pre><code class="language-(?P<lang>tikz|latex)">(?P<body>.*?)</code></pre>',
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


class _DropScripts:
    def __init__(self, source):
        self.source = source

    def __iter__(self):
        depth = 0
        for token in self.source:
            if token.get('name') == 'script':
                if token['type'] == 'StartTag':
                    depth += 1
                elif token['type'] == 'EndTag':
                    depth = max(0, depth - 1)
                continue
            if not depth:
                yield token


def _mark_legacy_tikz_blocks(html_value: str) -> str:
    def repl(match: re.Match[str]) -> str:
        body = match.group('body')
        if match.group('lang').lower() == 'tikz' or '\\begin{tikzpicture' in body or '\\begin{circuitikz' in body:
            return match.group(0).replace('<pre>', '<pre data-corvus-tikz>', 1)
        return match.group(0)

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


def _extract_math_segments(value: str, segments: list[_MathSegment] | None = None) -> tuple[str, list[_MathSegment]]:
    if segments is None:
        segments = []

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
    segments: list[_MathSegment] = []

    def prepare(text: str) -> str:
        text = _normalise_wiki_links(_normalise_legacy_math(text))
        return _extract_math_segments(text, segments)[0]

    # Keep math delimiters in code blocks literal, including TeX consumed by TikZJax.
    raw = raw.replace('\r\n', '\n').replace('\r', '\n')
    lines = raw.splitlines(keepends=True)
    parts = []
    start = 0
    for token in _markdown.parse(raw):
        if token.type in {'fence', 'code_block'} and token.map is not None:
            code_start, code_end = token.map
            parts.append(prepare(''.join(lines[start:code_start])))
            parts.append(''.join(lines[code_start:code_end]))
            start = code_end
    parts.append(prepare(''.join(lines[start:])))
    rendered = _markdown.render(''.join(parts))
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

    html_value = _render_markdown_cached(content)
    # Keep script elements in the parsed token stream solely so the filter can
    # discard their contents as well as the tags before HTML is serialized.
    cleaner = bleach.Cleaner(
        tags=[*_ALLOWED_TAGS, 'script'],
        attributes=_ALLOWED_ATTRIBUTES,
        protocols=_ALLOWED_PROTOCOLS,
        strip=True,
        filters=[_DropScripts],
    )
    cleaned = cleaner.clean(html_value)
    return _mark_legacy_tikz_blocks(_ensure_mathjax_markup(cleaned))
