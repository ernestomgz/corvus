from __future__ import annotations

import hashlib
import html
import io
import json
import posixpath
import re
import zipfile
import difflib
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple
from urllib.parse import unquote

import yaml
from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction
from django.utils import timezone
from markdown_it.helpers import parseLinkDestination, parseLinkTitle

from core.models import Card, Deck, ExternalId, Import, ImportSession, NoteSource
from core.services.decks import ensure_deck_path, plan_deck_path
from core.services.markdown_syntax import code_block_lines, code_mask, is_escaped, separate_numbered_lists
from core.services.note_sources import normalise_note_type, resolve_note_type, save_note_type, source_metadata

OBSIDIAN_LINK_PATTERN = re.compile(r'!\[\[(?P<path>[^\]]+)\]\]')
MEDIA_WIKI_PATTERN = re.compile(
    r'\[\[(?P<path>[^\]]+?\.(?:png|jpe?g|gif|svg|webp|mp3|wav|ogg|mp4|mov|m4a|flac))(?:\|[^\]]+)?\]\]',
    re.IGNORECASE,
)
ID_PATTERN = re.compile(r'^\s*id::\s*(?P<id>[\w:-]+)', re.IGNORECASE)
# Marker ids are the stable card identity used by current imports. Reverse markers may carry
# a pair like id:front_id|reverse_id so each generated card can be updated independently.
IMPORT_ID_PATTERN = re.compile(r'^[ \t]+id:(\S+)', re.IGNORECASE)
TAGS_PATTERN = re.compile(r'^\s*tags::\s*(?P<tags>.+)$', re.IGNORECASE)
IMAGE_OPEN_PATTERN = re.compile(r'!\[(?:\\.|[^\[\]\\\n]|\[[^\]\n]*\])*\]\([ \t]*')
WIKI_LINK_PATTERN = re.compile(r'\[\[[^\]\n]+\]\]')
HORIZONTAL_SPACE = re.compile(r'[ \t]*')
HEADING_CAPTURE_PATTERN = re.compile(r'^ {0,3}(?P<hashes>#{1,6})(?:[ \t]+|$)')
HEADING_CLOSE_PATTERN = re.compile(r'(?:^|[ \t]+)#+[ \t]*$')


@dataclass
class ParsedCard:
    front_md: str
    back_md: str
    context_md: str
    source_path: str
    source_anchor: str | None
    external_key: str
    tags: list[str]
    media: list[dict]
    deck_path: list[str]
    errors: list[str]
    marker_line: int
    marker_column: int
    marker_kind: str
    had_explicit_import_id: bool = False
    import_id: str | None = None


@dataclass
class MarkerRule:
    token: str
    long_card: bool
    reverse: bool


@dataclass
class MarkerResolver:
    pattern: re.Pattern
    lookup: dict[str, MarkerRule]


FIXED_MARKER_RULES: tuple[MarkerRule, ...] = (
    # Keep the accepted marker contract centralized; these are not user-configurable.
    MarkerRule('#long-card-reverse', long_card=True, reverse=True),
    MarkerRule('#card-reverse', long_card=False, reverse=True),
    MarkerRule('#long-card', long_card=True, reverse=False),
    MarkerRule('#card', long_card=False, reverse=False),
)


class MarkdownImportError(Exception):
    """Raised when the markdown importer encounters an unrecoverable problem."""


def _inline_diff_html(source: str, target: str, *, highlight_insert: bool) -> str:
    matcher = difflib.SequenceMatcher(None, source, target)
    parts: list[str] = []
    for tag, a0, a1, b0, b1 in matcher.get_opcodes():
        if tag == 'equal':
            parts.append(html.escape(source[a0:a1]))
        else:
            segment = target[b0:b1] if highlight_insert else source[a0:a1]
            css = 'bg-green-100 text-green-800' if highlight_insert else 'bg-red-100 text-red-800'
            parts.append(f'<mark class="{css} px-0.5 rounded-sm">{html.escape(segment)}</mark>')
    return ''.join(parts) or '&nbsp;'


def _render_diff_rows(before: str, after: str) -> list[dict]:
    before_lines = before.splitlines() or ['']
    after_lines = after.splitlines() or ['']
    matcher = difflib.SequenceMatcher(None, before_lines, after_lines)
    rows: list[dict] = []
    before_no = 1
    after_no = 1
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == 'equal':
            for offset in range(i2 - i1):
                rows.append({
                    'op': ' ',
                    'before': before_no,
                    'after': after_no,
                    'html': html.escape(before_lines[i1 + offset]) or '&nbsp;',
                })
                before_no += 1
                after_no += 1
        elif tag == 'delete':
            for line in before_lines[i1:i2]:
                rows.append({
                    'op': '-',
                    'before': before_no,
                    'after': '',
                    'html': _inline_diff_html(line, '', highlight_insert=False),
                })
                before_no += 1
        elif tag == 'insert':
            for line in after_lines[j1:j2]:
                rows.append({
                    'op': '+',
                    'before': '',
                    'after': after_no,
                    'html': _inline_diff_html('', line, highlight_insert=True),
                })
                after_no += 1
        elif tag == 'replace':
            span = max(i2 - i1, j2 - j1)
            for offset in range(span):
                before_line = before_lines[i1 + offset] if (i1 + offset) < i2 else ''
                after_line = after_lines[j1 + offset] if (j1 + offset) < j2 else ''
                if before_line:
                    rows.append({
                        'op': '-',
                        'before': before_no,
                        'after': '',
                        'html': _inline_diff_html(before_line, after_line, highlight_insert=False),
                    })
                    before_no += 1
                if after_line:
                    rows.append({
                        'op': '+',
                        'before': '',
                        'after': after_no,
                        'html': _inline_diff_html(before_line, after_line, highlight_insert=True),
                    })
                    after_no += 1
    return rows


def _note_deck_path(source_path: str) -> list[str]:
    *folders, filename = source_path.split('/')
    deck_folders = []
    for folder in folders:
        name = folder.replace('%', '%25')
        if name.lower().endswith('.md'):
            name = f'{name[:-3]}%2E{name[-2:]}'
        deck_folders.append(name)
    parts = [*deck_folders, filename]
    if any(len(part) > 255 for part in parts):
        raise MarkdownImportError(f"Deck name is too long for note '{source_path}'.")
    return parts


def _normalise_tags(raw: str | None) -> list[str]:
    if not raw:
        return []
    tags = [tag.strip() for tag in re.split(r'[;,]', raw) if tag.strip()]
    unique: list[str] = []
    for tag in tags:
        if tag not in unique:
            unique.append(tag)
    return unique


def _merge_tags(existing: list[str] | None, incoming: list[str] | None) -> list[str]:
    merged: list[str] = []
    for source in (existing or []):
        tag = (source or '').strip()
        if tag and tag not in merged:
            merged.append(tag)
    for source in (incoming or []):
        tag = (source or '').strip()
        if tag and tag not in merged:
            merged.append(tag)
    return merged


def _normalise_frontmatter_tags(raw: object) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        return _normalise_tags(raw)
    if isinstance(raw, (list, tuple)):
        unique: list[str] = []
        for item in raw:
            for tag in _normalise_tags(str(item)):
                if tag not in unique:
                    unique.append(tag)
        return unique
    return _normalise_tags(str(raw))


def _normalise_frontmatter_bool(raw: object, *, key: str, default: bool) -> bool:
    if raw is None:
        return default
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, int) and raw in {0, 1}:
        return bool(raw)
    if isinstance(raw, str):
        value = raw.strip().lower()
        if value in {'true', 'yes', 'on', '1'}:
            return True
        if value in {'false', 'no', 'off', '0'}:
            return False
    raise MarkdownImportError(f"YAML frontmatter '{key}' must be a boolean")


def _split_frontmatter(content: str) -> tuple[str, dict, int]:
    lines = content.splitlines()
    if not lines or lines[0].strip() != '---':
        return content, {}, 0

    for index in range(1, len(lines)):
        if lines[index].strip() in {'---', '...'}:
            raw_frontmatter = '\n'.join(lines[1:index])
            try:
                parsed = yaml.safe_load(raw_frontmatter) if raw_frontmatter.strip() else {}
            except yaml.YAMLError as exc:
                raise MarkdownImportError(f'Invalid YAML frontmatter: {exc}') from exc
            if parsed is None:
                parsed = {}
            if not isinstance(parsed, dict):
                raise MarkdownImportError('YAML frontmatter must be a mapping')
            body = '\n'.join(lines[index + 1 :])
            return body, parsed, index + 1

    return content, {}, 0


def _build_marker_resolver(user) -> MarkerResolver:
    lookup = {rule.token: rule for rule in FIXED_MARKER_RULES}
    unique_tokens = [rule.token for rule in FIXED_MARKER_RULES]
    joined = '|'.join(re.escape(token) for token in unique_tokens)
    # Markers must end at whitespace or end-of-line; this intentionally rejects
    # unsupported forms such as #card/reverse and ordinary words like #cardinal.
    marker_regex = re.compile(rf'(?<!\w)(?P<marker>{joined})(?=$|\s)', re.IGNORECASE)
    return MarkerResolver(pattern=marker_regex, lookup=lookup)


def _ensure_media_directory(user_id: int) -> Path:
    user_media_dir = Path(settings.MEDIA_ROOT) / str(user_id)
    user_media_dir.mkdir(parents=True, exist_ok=True)
    return user_media_dir


def _resolve_media(user_id: int, zip_file: zipfile.ZipFile, asset_path: str, *, write_media: bool) -> Tuple[str, dict] | None:
    try:
        data = zip_file.read(asset_path)
    except KeyError:
        return None
    digest = hashlib.sha1(data).hexdigest()
    suffix = Path(asset_path).suffix
    filename = f"{digest}{suffix}"
    if write_media:
        destination_path = _ensure_media_directory(user_id) / filename
        if not destination_path.exists():
            destination_path.write_bytes(data)
    url = f"{settings.MEDIA_URL}{user_id}/{filename}".replace('//', '/')
    return url, {'name': Path(asset_path).name, 'url': url, 'hash': digest}


def _image_destinations(text: str, mask: bytearray) -> Iterator[tuple[int, int, str]]:
    for match in IMAGE_OPEN_PATTERN.finditer(text):
        if mask[match.start()] or is_escaped(text, match.start()):
            continue
        start = match.end()
        destination = parseLinkDestination(text, start, len(text))
        if not destination.ok:
            continue
        end = destination.pos
        after = HORIZONTAL_SPACE.match(text, end).end()
        if after > end and text[after:after + 1] != ')':
            title = parseLinkTitle(text, after, len(text))
            if not title.ok:
                continue
            after = HORIZONTAL_SPACE.match(text, title.pos).end()
        if text[after:after + 1] == ')':
            mask[match.start():after + 1] = b'\1' * (after + 1 - match.start())
            yield start, end, unquote(destination.str)


def _rewrite_media_links(
    text: str, *, user_id: int, zip_file: zipfile.ZipFile, summary: dict, source_dir: str | None, write_media: bool,
) -> Tuple[str, list[dict], list[str]]:
    if '[' not in text:
        return text, [], []
    media_items: list[dict] = []
    missing_assets: list[str] = []

    def _register(asset: str) -> tuple[str, dict] | None:
        if not asset or re.match(r'^(?:[a-z][a-z\d+.-]*:|//)', asset, re.IGNORECASE):
            return None
        candidates = [asset.lstrip('/')]
        if source_dir and not asset.startswith('/'):
            candidates[:0] = [posixpath.join(source_dir, asset), posixpath.join(source_dir, 'attachments', asset)]
        for candidate in dict.fromkeys(posixpath.normpath(path) for path in candidates):
            if candidate in {'.', '..'} or candidate.startswith('../'):
                continue
            result = _resolve_media(user_id, zip_file, candidate, write_media=write_media)
            if result is None:
                continue
            url, media_meta = result
            if media_meta not in media_items:
                media_items.append(media_meta)
                summary['media_copied'] += 1
            return url, media_meta
        missing_assets.append(asset)
        return None

    mask = code_mask(text)
    edits = []
    for start, end, target in _image_destinations(text, mask):
        registered = _register(target)
        if registered:
            edits.append((start, end, registered[0]))

    for pattern in (OBSIDIAN_LINK_PATTERN, MEDIA_WIKI_PATTERN):
        for match in pattern.finditer(text):
            if mask[match.start()] or is_escaped(text, match.start()):
                continue
            target = match['path'].split('|', 1)[0].strip()
            registered = _register(target)
            if registered:
                alt = Path(target).stem.replace('[', '\\[').replace(']', '\\]') or 'image'
                edits.append((match.start(), match.end(), f'![{alt}]({registered[0]})'))
            mask[match.start():match.end()] = b'\1' * len(match[0])

    rewritten = text
    for start, end, replacement in sorted(edits, reverse=True):
        rewritten = rewritten[:start] + replacement + rewritten[end:]
    return rewritten, media_items, missing_assets


INLINE_MARKER_PATTERNS = [
    re.compile(r'^\*\*(?P<text>.+?)\*\*$'),
    re.compile(r'^__(?P<text>.+?)__$'),
    re.compile(r'^\*(?P<text>.+?)\*$'),
    re.compile(r'^_(?P<text>.+?)_$'),
]


def _clean_front_text(value: str) -> str:
    value = value.strip('\n').rstrip()
    if '\n' in value or value.startswith(('    ', '\t')):
        return value
    value = value.lstrip()
    while True:
        for pattern in INLINE_MARKER_PATTERNS:
            match = pattern.match(value)
            if match:
                value = match.group('text').strip()
                break
        else:
            break
    return value.strip()


def _compose_front_text(body: str, context: str) -> str:
    body = (body or '').strip('\n').rstrip()
    context = (context or '').strip()
    if context and body:
        separator = '\n\n' if re.match(r'^(?: {4}|\t| {0,3}(?:\d+[.)]|[-+*])[ \t])', body) else '\n'
        return f'{context}{separator}{body}'
    if context:
        return context
    return body


def _generate_external_key(source_path: str, line_no: int, front_md: str) -> str:
    """Generate a deterministic external key for cards without an id:: anchor."""
    # The generated key is only for ExternalId uniqueness/writeback bookkeeping.
    # It is not an update identity; no-id pushes should create new cards.
    key_base = f"{source_path}:{line_no}"
    key_hash = hashlib.md5(key_base.encode('utf-8')).hexdigest()[:8]
    return f"md_{key_hash}"


def _split_import_id_value(raw_value: str, *, reverse_flag: bool) -> tuple[str | None, str | None]:
    value = (raw_value or '').strip().lower()
    if not value:
        return None, None
    # Reverse markers can carry two ids: the normal card first, then the reverse card.
    # Without the second id, the reverse card receives a new generated import_id.
    if reverse_flag and '|' in value:
        forward_raw, reverse_raw = value.split('|', 1)
        forward_id = forward_raw.strip() or None
        reverse_id = reverse_raw.strip() or None
        return forward_id, reverse_id
    return value, None


def _update_heading_stack(stack: list[tuple[int, str]], level: int, title: str) -> None:
    while stack and stack[-1][0] >= level:
        stack.pop()
    if title:
        stack.append((level, title))


def _heading_title(value: str) -> str:
    return _clean_front_text(HEADING_CLOSE_PATTERN.sub('', value.strip()))


def _has_card_content(value: str) -> bool:
    # Comments and property lines alone cannot supply a question or answer.
    if not value.strip():
        return False
    prose = re.sub(r'<!--.*?-->', '', value, flags=re.DOTALL)
    has_prose = any(line.strip() and not (ID_PATTERN.match(line) or TAGS_PATTERN.match(line)) for line in prose.splitlines())
    return has_prose or bool(code_block_lines(value))


def _parse_markdown_cards(
    content: str,
    source_path: str,
    *,
    user_id: int,
    zip_file: zipfile.ZipFile,
    summary: dict,
    resolver: MarkerResolver,
    deck_path: list[str] | None = None,
    write_media: bool = True,
) -> List[ParsedCard]:
    content, frontmatter, line_offset = _split_frontmatter(content)
    file_tags = _normalise_frontmatter_tags(frontmatter.get('card-tags'))
    include_heading_context = _normalise_frontmatter_bool(
        frontmatter.get('card-heading-context'),
        key='card-heading-context',
        default=True,
    )
    lines = content.splitlines()
    block_lines = code_block_lines(content)
    mask = code_mask(content, block_lines)
    for _ in _image_destinations(content, mask):
        pass  # A marker inside an image label or destination is literal.
    for match in WIKI_LINK_PATTERN.finditer(content):
        if not is_escaped(content, match.start()):
            mask[match.start():match.end()] = b'\1' * len(match[0])
    markers = {}
    offset = 0
    for index, line in enumerate(lines):
        matches = [
            match for match in resolver.pattern.finditer(line)
            if not mask[offset + match.start()] and not is_escaped(line, match.start())
        ]
        if matches:
            markers[index] = matches
        offset += len(line) + 1

    parsed_cards: list[ParsedCard] = []
    deck_parts = deck_path or []
    source_dir = posixpath.dirname(source_path)
    i = 0
    previous_card_end = 0
    heading_stack: list[tuple[int, str]] = []
    last_heading_line = None
    while i < len(lines):
        line = lines[i]
        heading = HEADING_CAPTURE_PATTERN.match(line) if i not in block_lines else None
        matches = markers.get(i)
        if not matches:
            if heading:
                title = _heading_title(line[heading.end():])
                _update_heading_stack(heading_stack, len(heading['hashes']), title)
                last_heading_line = i if title else None
            i += 1
            continue

        marker_match = matches[0]
        line_no = line_offset + i + 1
        marker_start = marker_match.start()
        marker_end = marker_match.end()
        marker_column = len(line[:marker_start].encode('utf-16-le')) // 2
        rule = resolver.lookup[marker_match['marker'].lower()]
        card_errors: list[str] = []
        if len(matches) > 1:
            card_errors.append('Use one card marker per line.')

        import_id = None
        reverse_import_id = None
        front_after = line[marker_end:]
        import_id_match = IMPORT_ID_PATTERN.match(front_after)
        if import_id_match:
            import_id, reverse_import_id = _split_import_id_value(
                import_id_match[1], reverse_flag=rule.reverse,
            )
            front_after = front_after[import_id_match.end():]

        context_titles = [title for _, title in heading_stack]
        if heading:
            level = len(heading['hashes'])
            title = _heading_title(line[heading.end():marker_start] or front_after)
            front_content = title
            context_titles = [parent for depth, parent in heading_stack if depth < level]
            _update_heading_stack(heading_stack, level, title)
            last_heading_line = i if title else None
        else:
            front_content = _clean_front_text(line[:marker_start].strip() or front_after.strip())
            if not front_content:
                start = i
                while start > previous_card_end:
                    index = start - 1
                    if index not in block_lines and (not lines[index].strip() or HEADING_CAPTURE_PATTERN.match(lines[index])):
                        break
                    start = index
                if start < i:
                    front_content = _clean_front_text('\n'.join(lines[start:i]))
                elif last_heading_line is not None and last_heading_line >= previous_card_end and all(
                    not item.strip() for item in lines[last_heading_line + 1:i]
                ):
                    front_content = heading_stack[-1][1]
            if context_titles and context_titles[-1].casefold() == front_content.casefold():
                context_titles.pop()

        i += 1
        anchor = None
        tags: list[str] = list(file_tags)

        if i < len(lines):
            anchor_match = ID_PATTERN.match(lines[i])
            if anchor_match:
                anchor = anchor_match.group('id').strip()
                i += 1

        if i < len(lines):
            tags_match = TAGS_PATTERN.match(lines[i])
            if tags_match:
                tags = _merge_tags(file_tags, _normalise_tags(tags_match.group('tags')))
                i += 1
        while i < len(lines) and not lines[i].strip():
            i += 1

        back_lines: list[str] = []
        consecutive_blank = 0

        # Short cards stop at the first blank line. Long cards preserve single blank
        # lines and stop only after two consecutive blanks outside fenced code blocks.
        while i < len(lines):
            candidate = lines[i]
            if i in markers:
                break
            if not candidate.strip() and i not in block_lines:
                consecutive_blank += 1
                if not rule.long_card or consecutive_blank >= 2:
                    break
            else:
                consecutive_blank = 0
            back_lines.append(candidate)
            i += 1

        previous_card_end = i
        back_md_raw = '\n'.join(back_lines).rstrip()
        if not _has_card_content(front_content):
            card_errors.append('Missing question.')
        if not _has_card_content(back_md_raw):
            card_errors.append('Missing answer.')
        context_md = ' > '.join(context_titles) if include_heading_context else ''

        front_md, media_front, missing_front = _rewrite_media_links(
            separate_numbered_lists(front_content), user_id=user_id, zip_file=zip_file, summary=summary,
            source_dir=source_dir, write_media=write_media,
        )
        back_md, media_back, missing_back = _rewrite_media_links(
            separate_numbered_lists(back_md_raw), user_id=user_id, zip_file=zip_file, summary=summary,
            source_dir=source_dir, write_media=write_media,
        )

        media: list[dict] = []
        for item in media_front + media_back:
            if item not in media:
                media.append(item)

        if missing_front or missing_back:
            missing_all = sorted(set(missing_front + missing_back))
            card_errors.append(
                f"Missing attachment(s): {', '.join(missing_all)}. Expected inside an 'attachments' folder next to {source_path}."
            )

        # id:: anchors are preserved as external IDs for old imports. Without id:: we
        # generate an external key, but updates still require explicit marker id:.
        external_key = anchor or _generate_external_key(source_path, line_no, front_md)
        display_front_md = _compose_front_text(front_md, context_md)
        base_card = ParsedCard(
            front_md=front_md,
            back_md=back_md,
            context_md=context_md,
            source_path=source_path,
            source_anchor=anchor,
            external_key=external_key,
            tags=tags,
            media=media,
            deck_path=list(deck_parts),
            errors=list(card_errors),
            marker_line=line_no,
            marker_column=marker_column,
            marker_kind='card',
            had_explicit_import_id=bool(import_id),
            import_id=import_id,
        )
        parsed_cards.append(base_card)
        if rule.reverse:
            # The reverse copy is a separate card with its own identity. Its back uses
            # the composed front so heading context is retained when front/back swap.
            reverse_key = f"{external_key}__reverse"
            parsed_cards.append(
                ParsedCard(
                    front_md=back_md,
                    back_md=display_front_md,
                    context_md='',
                    source_path=source_path,
                    source_anchor=anchor,
                    external_key=reverse_key,
                    tags=tags,
                    media=media,
                    deck_path=list(deck_parts),
                    errors=list(card_errors),
                    marker_line=line_no,
                    marker_column=marker_column,
                    marker_kind='reverse',
                    had_explicit_import_id=bool(reverse_import_id),
                    import_id=reverse_import_id,
                )
            )
    return parsed_cards


def _collect_markdown_cards(*, user_id: int, uploaded_file, resolver: MarkerResolver, write_media: bool) -> Tuple[List[ParsedCard], dict]:
    summary = {'media_copied': 0}
    data = uploaded_file.read()
    try:
        primary_buffer = io.BytesIO(data)
        archive = zipfile.ZipFile(primary_buffer)
        buffers: list[io.BytesIO] = [primary_buffer]
    except zipfile.BadZipFile:
        filename = getattr(uploaded_file, 'name', None) or 'notes.md'
        if not filename.lower().endswith('.md'):
            filename = f"{filename}.md"
        text_data = data.decode('utf-8', 'ignore')
        temp_buffer = io.BytesIO()
        with zipfile.ZipFile(temp_buffer, 'w') as tmp_zip:
            tmp_zip.writestr(filename, text_data)
        temp_buffer.seek(0)
        archive = zipfile.ZipFile(temp_buffer)
        buffers = [temp_buffer]
    parsed_cards: list[ParsedCard] = []
    with archive:
        for info in archive.infolist():
            if info.is_dir() or not info.filename.lower().endswith('.md'):
                continue
            raw_bytes = archive.read(info)
            try:
                markdown_text = raw_bytes.decode('utf-8')
            except UnicodeDecodeError:
                markdown_text = raw_bytes.decode('utf-8', 'ignore')
            source_path = _normalise_archive_member_path(info.filename)
            parsed_cards.extend(
                _parse_markdown_cards(
                    markdown_text,
                    source_path,
                    user_id=user_id,
                    zip_file=archive,
                    summary=summary,
                    resolver=resolver,
                    deck_path=_note_deck_path(source_path),
                    write_media=write_media,
                )
            )
    for buffer in buffers:
        buffer.close()
    return parsed_cards, summary


def _normalise_archive_member_path(value: str) -> str:
    path = value.replace('\\', '/').removeprefix('./')
    parts = path.split('/')
    if re.match(r'^[A-Za-z]:', path) or any(
        part in {'', '.', '..'} or part != part.strip() or '\x00' in part for part in parts
    ):
        raise MarkdownImportError(f"Expected a relative file path, got '{value}'.")
    return path


def _build_uploaded_archive_from_note(
    *,
    source_path: str,
    content: str,
    attachments: dict[str, bytes] | None = None,
):
    normalised_source_path = _normalise_archive_member_path(source_path)
    if not normalised_source_path.lower().endswith('.md'):
        raise MarkdownImportError('source_path must end with .md')

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as zf:
        zf.writestr(normalised_source_path, content)
        for attachment_path, data in sorted((attachments or {}).items()):
            normalised_attachment_path = _normalise_archive_member_path(attachment_path)
            if normalised_attachment_path == normalised_source_path:
                raise MarkdownImportError('attachment path conflicts with source_path')
            zf.writestr(normalised_attachment_path, data)
    buffer.seek(0)
    filename = f"{Path(normalised_source_path).stem or 'note'}.zip"
    return SimpleUploadedFile(filename, buffer.read(), content_type='application/zip')


def _build_markdown_payload(*, user, deck: Deck | None, uploaded_file, audit: bool = False) -> dict:
    resolver = _build_marker_resolver(user)
    parsed_cards, parse_summary = _collect_markdown_cards(
        user_id=user.id, uploaded_file=uploaded_file, resolver=resolver, write_media=not audit,
    )

    # Only explicit marker ids participate in existing-card lookup. Auto-generated ids
    # are assigned after this so writeback has an id, but they must not trigger updates.
    import_ids = [card.import_id for card in parsed_cards if card.import_id]

    import_id_counts = Counter(import_ids)
    for card in parsed_cards:
        if card.import_id and import_id_counts[card.import_id] > 1:
            card.errors.append(f"Import ID '{card.import_id}' is used by multiple cards in this import. Each card must have a unique import ID.")

    existing_import_id_map = {}
    if import_ids:
        existing_import_id_map = {
            card.import_id: card
            for card in Card.objects.filter(user=user, import_id__in=import_ids)
        }

    used_ids = set() if audit else set(Card.objects.filter(import_id__isnull=False).values_list('import_id', flat=True))
    # Reserve ids from this batch before filling gaps so generated ids cannot collide
    # with explicit ids later in the same file.
    for card in parsed_cards:
        if card.import_id:
            used_ids.add(card.import_id)

    for card in parsed_cards:
        if not card.import_id and not audit:
            next_id = 1
            while True:
                candidate = hex(next_id)[2:].lower()
                if candidate not in used_ids:
                    card.import_id = candidate
                    used_ids.add(candidate)
                    break
                next_id += 1
        elif card.import_id:
            try:
                int(card.import_id, 16)
            except ValueError:
                card.errors.append(f"Invalid import ID format: '{card.import_id}'. Must be hexadecimal.")

    import_id_counts: dict[str, list[int]] = {}
    for idx, card in enumerate(parsed_cards):
        if card.import_id:
            import_id_counts.setdefault(card.import_id, []).append(idx)

    for import_id, indices in import_id_counts.items():
        if len(indices) > 1:
            for idx in indices:
                parsed_cards[idx].errors.append(
                    f"Duplicate import ID '{import_id}' found in this import (also used in card at index {[i for i in indices if i != idx]}). Each card must have a unique ID."
                )

    # External IDs only update legacy id:: imports. Generated md_* keys may collide
    # across separate no-id pushes and are handled as uniqueness conflicts during apply.
    external_keys = [card.external_key for card in parsed_cards]
    existing_map = {
        external.external_key: external
        for external in ExternalId.objects.select_related('card', 'card__deck').filter(
            system='logseq', external_key__in=external_keys
        )
    }

    session_cards: list[dict] = []
    create_count = 0
    update_count = 0
    unchanged_count = 0
    diff_count = 0
    has_invalid_cards = False
    for index, parsed in enumerate(parsed_cards):
        target_deck_path = '/'.join([deck.full_path(), *parsed.deck_path] if deck else parsed.deck_path)
        card_errors = list(parsed.errors)
        card_warnings = []
        if card_errors:
            has_invalid_cards = True
        external = None
        # id:: is the legacy identity path. Marker id: wins when both are present.
        if parsed.source_anchor and not parsed.had_explicit_import_id:
            external = existing_map.get(parsed.external_key)
        existing_payload = None
        has_changes = False
        incoming_tags = list(parsed.tags)
        display_tags = incoming_tags
        display_front = _compose_front_text(parsed.front_md, parsed.context_md)
        display_back = parsed.back_md
        existing_card = None
        import_id_conflict = None
        if parsed.had_explicit_import_id and parsed.import_id in existing_import_id_map:
            import_id_conflict = existing_import_id_map[parsed.import_id]
            if import_id_conflict != existing_card:
                card_warnings.append(f"Import ID '{parsed.import_id}' already exists. This will update that card.")

        if import_id_conflict:
            existing_card = import_id_conflict
        elif external and external.card.user == user:
            existing_card = external.card
        if existing_card:
            display_tags = incoming_tags
            existing_deck_path = existing_card.deck.full_path()
            metadata_changes = {}
            if existing_deck_path != target_deck_path:
                metadata_changes['deck'] = {
                    'from': existing_deck_path,
                    'to': target_deck_path,
                }
            if (existing_card.source_path or '') != (parsed.source_path or ''):
                metadata_changes['source_path'] = {
                    'from': existing_card.source_path or '',
                    'to': parsed.source_path or '',
                }
            if (existing_card.source_anchor or '') != (parsed.source_anchor or ''):
                metadata_changes['source_anchor'] = {
                    'from': existing_card.source_anchor or '',
                    'to': parsed.source_anchor or '',
                }
            existing_payload = {
                'card_id': str(existing_card.id),
                'import_id': existing_card.import_id,
                'deck_id': existing_card.deck_id,
                'deck_full_path': existing_deck_path,
                'front_md': existing_card.front_md,
                'back_md': existing_card.back_md,
                'tags': existing_card.tags,
                'media': existing_card.media,
                'source_path': existing_card.source_path,
                'source_anchor': existing_card.source_anchor,
            }
            has_changes = (
                existing_card.front_md != display_front
                or existing_card.back_md != display_back
                or existing_card.tags != display_tags
                or existing_card.media != parsed.media
                or bool(metadata_changes)
            )
            if has_changes:
                update_count += 1
                diff_count += 1
            else:
                unchanged_count += 1
        else:
            existing_payload = None
            metadata_changes = {}
            has_changes = True
            create_count += 1

        diff_payload = None
        if existing_payload and has_changes and not audit:
            diff_payload = {
                'front': _render_diff_rows(existing_payload['front_md'], display_front),
                'back': _render_diff_rows(existing_payload['back_md'], display_back),
            }

        session_cards.append(
            {
                'index': index,
                'external_key': parsed.external_key,
                'import_id': parsed.import_id,
                'front_md': display_front,
                'back_md': display_back,
                'tags': display_tags,
                'media': parsed.media,
                'source_path': parsed.source_path,
                'source_anchor': parsed.source_anchor,
                'marker_line': parsed.marker_line,
                'marker_column': parsed.marker_column,
                'marker_kind': parsed.marker_kind,
                'had_explicit_import_id': parsed.had_explicit_import_id,
                'deck_path': parsed.deck_path,
                'target_deck_path': target_deck_path,
                'existing': existing_payload,
                'has_changes': has_changes,
                'unchanged': bool(existing_payload and not has_changes),
                'metadata_changes': metadata_changes,
                'context': parsed.context_md,
                'errors': card_errors,
                'warnings': card_warnings,
                'diff': diff_payload,
            }
        )

    return {
        'root_deck_id': deck.id if deck else None,
        'cards': session_cards,
        'summary': {
            'creates': create_count,
            'updates': update_count,
            'unchanged': unchanged_count,
            'conflicts': diff_count,
            'media_copied': parse_summary.get('media_copied', 0),
            'has_errors': has_invalid_cards,
        },
    }


def prepare_markdown_session(*, user, deck: Deck | None, uploaded_file) -> ImportSession:
    payload = _build_markdown_payload(user=user, deck=deck, uploaded_file=uploaded_file)
    total = len(payload['cards'])
    return ImportSession.objects.create(
        user=user,
        kind='markdown',
        status='ready',
        source_name=getattr(uploaded_file, 'name', ''),
        total=total,
        processed=total,
        payload=payload,
    )


def _obsidian_note_type(content: str) -> tuple[str, int]:
    _, frontmatter, _ = _split_frontmatter(content)
    raw_type = frontmatter.get('note-type')
    if raw_type is None or raw_type == '':
        return NoteSource.QUESTIONS, NoteSource.DEFAULT
    try:
        return normalise_note_type(raw_type), NoteSource.EXPLICIT
    except ValueError as exc:
        raise MarkdownImportError(str(exc)) from exc


def _note_type_change(user, root_deck: Deck, source_path: str, note_type: str, priority: int) -> str | None:
    if priority == NoteSource.DEFAULT:
        return None
    saved = source_metadata(user, root_deck, [source_path]).get(source_path)
    effective_type, _ = resolve_note_type(saved, note_type, priority)
    if effective_type != (saved.note_type if saved else NoteSource.QUESTIONS):
        return effective_type
    return None


def audit_markdown_note(*, user, root_deck: Deck, source_path: str, content: str, attachments: dict[str, bytes]) -> dict:
    type_change = None
    errors_by_line = {}
    try:
        upload = _build_uploaded_archive_from_note(source_path=source_path, content=content, attachments=attachments)
        note_type, priority = _obsidian_note_type(content)
        cards = _build_markdown_payload(user=user, deck=root_deck, uploaded_file=upload, audit=True)['cards']
        errors = list(dict.fromkeys(error for card in cards for error in card['errors']))
        for card in cards:
            if card['errors']:
                errors_by_line.setdefault(card['marker_line'], {}).update(dict.fromkeys(card['errors']))
        if cards and not errors:
            type_change = _note_type_change(
                user, root_deck, _normalise_archive_member_path(source_path), note_type, priority,
            )
    except MarkdownImportError as exc:
        cards = []
        errors = [str(exc)]
    identified = [card for card in cards if card['had_explicit_import_id'] or card['source_anchor']]
    matched = [card for card in identified if card['existing']]
    updates = [card for card in matched if card['has_changes'] and not card['errors']]
    result = {
        'identified': len(identified),
        'identities': [
            {'kind': 'marker', 'value': card['import_id']}
            if card['had_explicit_import_id'] else {'kind': 'anchor', 'value': card['source_anchor']}
            for card in identified
        ],
        'found': len(matched),
        'updates': len(updates),
        'moves': sum('deck' in card['metadata_changes'] for card in updates),
        'unchanged': sum(card['unchanged'] and not card['errors'] for card in matched),
        'without_ids': len(cards) - len(identified),
        'errors': errors,
        **({'note_type_change': type_change} if type_change else {}),
    }
    if errors_by_line:
        result['error_locations'] = [
            {'marker_line': line, 'errors': list(messages)} for line, messages in errors_by_line.items()
        ]
    return result


def prepare_markdown_note_session(
    *,
    user,
    root_deck: Deck,
    source_path: str,
    content: str,
    attachments: dict[str, bytes] | None = None,
    source_hash: str = '',
) -> ImportSession:
    normalised_source_path = _normalise_archive_member_path(source_path)
    note_type, priority = _obsidian_note_type(content)
    uploaded_file = _build_uploaded_archive_from_note(
        source_path=normalised_source_path,
        content=content,
        attachments=attachments,
    )
    session = prepare_markdown_session(user=user, deck=root_deck, uploaded_file=uploaded_file)

    payload = dict(session.payload or {})
    planned_decks: list[str] = []
    seen_paths: set[tuple[str, ...]] = set()
    for card in payload.get('cards', []):
        deck_parts = tuple(card['deck_path'])
        if deck_parts in seen_paths:
            continue
        seen_paths.add(deck_parts)
        planned_decks.extend(plan_deck_path(user, root_deck, list(deck_parts)))

    payload['obsidian'] = {
        'source_system': 'obsidian',
        'source_hash': source_hash,
        'source_path': normalised_source_path,
        'root_deck_id': root_deck.id,
        'root_deck_path': root_deck.full_path(),
        'planned_decks': planned_decks,
        'note_type': note_type,
        'type_priority': priority,
        'note_type_change': _note_type_change(user, root_deck, normalised_source_path, note_type, priority),
    }
    session.source_name = normalised_source_path
    session.payload = payload
    session.save(update_fields=['source_name', 'payload', 'updated_at'])
    return session


def apply_markdown_session(session: ImportSession, *, decisions: Dict[int, str] | None = None) -> Import:
    if session.status != 'ready':
        raise MarkdownImportError('Import session is not ready to apply.')

    payload = dict(session.payload or {})
    cards_payload = list(payload.get('cards', []))
    if not cards_payload:
        raise MarkdownImportError('Import session payload is empty.')

    invalid_cards = [card for card in cards_payload if card.get('errors')]
    if invalid_cards:
        card = invalid_cards[0]
        raise MarkdownImportError(
            f"Resolve the card errors before applying this import. {card['source_path']} line {card['marker_line']}: {card['errors'][0]}"
        )

    root_deck_id = payload.get('root_deck_id')
    root_deck = None
    if root_deck_id:
        try:
            root_deck = Deck.objects.get(id=root_deck_id, user=session.user)
        except Deck.DoesNotExist as exc:
            raise MarkdownImportError('Root deck no longer exists.') from exc

    decisions = decisions or {}
    summary = {
        'created': 0,
        'updated': 0,
        'skipped': 0,
        'media_copied': payload.get('summary', {}).get('media_copied', 0),
        'decks_created': 0,
    }
    applied_cards: list[dict[str, object]] = []

    deck_cache: dict[tuple[str, ...], Deck] = {}
    if root_deck:
        deck_cache[()] = root_deck

    def resolve_deck(parts: list[str]) -> tuple[Deck, list[Deck]]:
        key = tuple(parts)
        if key in deck_cache:
            return deck_cache[key], []
        try:
            target, created = ensure_deck_path(session.user, root_deck, parts)
        except ValueError as exc:
            raise MarkdownImportError(str(exc)) from exc
        deck_cache[key] = target
        return target, created

    def _unique_external_key(base_key: str) -> str:
        # ExternalId.external_key is globally unique. When a no-id push reuses the
        # deterministic md_* key, create a scoped variant instead of updating.
        key = base_key
        counter = 0
        while ExternalId.objects.filter(external_key=key).exists():
            counter += 1
            key = f"{base_key}__u{session.user_id}_{counter}"
        return key

    with transaction.atomic():
        for card_data in cards_payload:
            index = card_data.get('index')
            decision = decisions.get(index, 'imported')
            if card_data.get('unchanged'):
                summary['skipped'] += 1
                applied_cards.append(
                    {
                        'index': index,
                        'status': 'unchanged',
                        'import_id': card_data.get('import_id'),
                        'card_id': (card_data.get('existing') or {}).get('card_id'),
                        'marker_line': card_data.get('marker_line'),
                        'marker_kind': card_data.get('marker_kind'),
                        'source_path': card_data.get('source_path'),
                    }
                )
                continue
            if decision in {'existing', 'skip'}:
                existing = card_data.get('existing') or {}
                summary['skipped'] += 1
                applied_cards.append(
                    {
                        'index': index,
                        'status': 'skipped',
                        'import_id': existing.get('import_id'),
                        'card_id': existing.get('card_id'),
                        'marker_line': card_data.get('marker_line'),
                        'marker_kind': card_data.get('marker_kind'),
                        'source_path': card_data.get('source_path'),
                    }
                )
                continue
            target_deck, created_decks = resolve_deck(card_data['deck_path'])
            if created_decks:
                summary['decks_created'] += len(created_decks)

            existing_payload = card_data.get('existing')
            if not existing_payload:
                raw_external_key = card_data['external_key']
                existing_ext = ExternalId.objects.filter(external_key=raw_external_key).select_related('card').first()
                # Keep id:: support for old imports. Generated source-location keys are
                # bookkeeping only and must not turn a later no-id push into an update.
                can_update_by_external = bool(card_data.get('source_anchor')) and not bool(
                    card_data.get('had_explicit_import_id')
                )
                if (
                    can_update_by_external
                    and existing_ext
                    and existing_ext.card
                    and existing_ext.card.user_id == session.user_id
                ):
                    existing_card = existing_ext.card
                    existing_card.front_md = card_data['front_md']
                    existing_card.back_md = card_data['back_md']
                    existing_card.tags = card_data.get('tags', [])
                    existing_card.media = card_data.get('media', [])
                    existing_card.source_path = card_data.get('source_path')
                    existing_card.source_anchor = card_data.get('source_anchor')
                    existing_card.deck = target_deck
                    existing_card.import_id = card_data.get('import_id')
                    existing_card.save(
                        update_fields=[
                            'front_md',
                            'back_md',
                            'tags',
                            'media',
                            'source_path',
                            'source_anchor',
                            'deck',
                            'import_id',
                            'updated_at',
                        ]
                    )
                    ExternalId.objects.get_or_create(
                        card=existing_card,
                        system='logseq',
                        external_key=raw_external_key,
                        defaults={'extra': {}},
                    )
                    summary['updated'] += 1
                    applied_cards.append(
                        {
                            'index': index,
                            'status': 'updated',
                            'import_id': existing_card.import_id,
                            'card_id': str(existing_card.id),
                            'marker_line': card_data.get('marker_line'),
                            'marker_kind': card_data.get('marker_kind'),
                            'source_path': card_data.get('source_path'),
                        }
                    )
                    continue
                external_key = raw_external_key
                if existing_ext and existing_ext.card:
                    external_key = _unique_external_key(f"{raw_external_key}__u{session.user_id}")
                card = Card.objects.create(
                    user=session.user,
                    deck=target_deck,
                    front_md=card_data['front_md'],
                    back_md=card_data['back_md'],
                    tags=card_data.get('tags', []),
                    media=card_data.get('media', []),
                    source_path=card_data.get('source_path'),
                    source_anchor=card_data.get('source_anchor'),
                    import_id=card_data.get('import_id'),
                )
                ExternalId.objects.get_or_create(
                    card=card,
                    system='logseq',
                    external_key=external_key,
                    defaults={'extra': {}},
                )
                summary['created'] += 1
                applied_cards.append(
                    {
                        'index': index,
                        'status': 'created',
                        'import_id': card.import_id,
                        'card_id': str(card.id),
                        'marker_line': card_data.get('marker_line'),
                        'marker_kind': card_data.get('marker_kind'),
                        'source_path': card_data.get('source_path'),
                    }
                )
                continue

            try:
                existing_card = Card.objects.get(id=existing_payload['card_id'], user=session.user)
            except (KeyError, Card.DoesNotExist):
                # Card disappeared; treat as new card.
                card = Card.objects.create(
                    user=session.user,
                    deck=target_deck,
                    front_md=card_data['front_md'],
                    back_md=card_data['back_md'],
                    tags=card_data.get('tags', []),
                    media=card_data.get('media', []),
                    source_path=card_data.get('source_path'),
                    source_anchor=card_data.get('source_anchor'),
                    import_id=card_data.get('import_id'),
                )
                recovery_key = card_data['external_key']
                conflict = ExternalId.objects.filter(external_key=recovery_key).exclude(card__user_id=session.user_id).exists()
                if conflict:
                    recovery_key = _unique_external_key(f"{recovery_key}__u{session.user_id}")
                ExternalId.objects.get_or_create(card=card, system='logseq', external_key=recovery_key)
                summary['created'] += 1
                applied_cards.append(
                    {
                        'index': index,
                        'status': 'created',
                        'import_id': card.import_id,
                        'card_id': str(card.id),
                        'marker_line': card_data.get('marker_line'),
                        'marker_kind': card_data.get('marker_kind'),
                        'source_path': card_data.get('source_path'),
                    }
                )
                continue

            existing_card.front_md = card_data['front_md']
            existing_card.back_md = card_data['back_md']
            existing_card.tags = card_data.get('tags', [])
            existing_card.media = card_data.get('media', [])
            existing_card.source_path = card_data.get('source_path')
            existing_card.source_anchor = card_data.get('source_anchor')
            existing_card.deck = target_deck
            existing_card.import_id = card_data.get('import_id')
            existing_card.save(
                update_fields=[
                    'front_md',
                    'back_md',
                    'tags',
                    'media',
                    'source_path',
                    'source_anchor',
                    'deck',
                    'import_id',
                    'updated_at',
                ]
            )
            safe_external_key = card_data['external_key']
            if ExternalId.objects.filter(external_key=safe_external_key).exclude(card_id=existing_card.id).exists():
                safe_external_key = _unique_external_key(f"{safe_external_key}__u{session.user_id}")
            ExternalId.objects.get_or_create(
                card=existing_card,
                system='logseq',
                external_key=safe_external_key,
                defaults={'extra': {}},
            )
            summary['updated'] += 1
            applied_cards.append(
                {
                    'index': index,
                    'status': 'updated',
                    'import_id': existing_card.import_id,
                    'card_id': str(existing_card.id),
                    'marker_line': card_data.get('marker_line'),
                    'marker_kind': card_data.get('marker_kind'),
                    'source_path': card_data.get('source_path'),
                }
            )

        obsidian = payload.get('obsidian')
        if obsidian and root_deck and Card.objects.filter(
            user=session.user, source_path=obsidian['source_path'], deck_id__in=root_deck.descendant_ids(),
        ).exists():
            save_note_type(
                session.user, root_deck, obsidian['source_path'], obsidian.get('note_type', NoteSource.QUESTIONS),
                obsidian.get('type_priority', NoteSource.DEFAULT),
            )

        import_record = Import.objects.create(
            user=session.user,
            kind='markdown',
            status='ok',
            summary=summary,
            created_at=timezone.now(),
        )
        payload['result'] = summary
        payload['apply_results'] = applied_cards
        session.status = 'applied'
        session.import_record = import_record
        session.payload = payload
        session.processed = session.total
        session.save(update_fields=['status', 'import_record', 'payload', 'processed', 'updated_at'])
        return import_record


def cancel_markdown_session(session: ImportSession) -> None:
    if session.status != 'ready':
        return
    session.status = 'cancelled'
    session.save(update_fields=['status', 'updated_at'])


@transaction.atomic
def process_markdown_archive(*, user, deck: Deck, uploaded_file) -> dict:
    session = prepare_markdown_session(user=user, deck=deck, uploaded_file=uploaded_file)
    import_record = apply_markdown_session(session)
    return import_record
