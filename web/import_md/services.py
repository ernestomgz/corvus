from __future__ import annotations

import hashlib
import html
import io
import json
import re
import zipfile
import difflib
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Tuple

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from core.models import Card, CardImportFormat, Deck, ExternalId, Import, ImportSession
from core.services.cards import infer_card_type
from core.services.card_types import resolve_card_type
from core.services.decks import ensure_deck_path, plan_deck_path

OBSIDIAN_LINK_PATTERN = re.compile(r'!\[\[(?P<path>[^\]]+)\]\]')
MEDIA_WIKI_PATTERN = re.compile(
    r'\[\[(?P<path>[^\]]+?\.(?:png|jpe?g|gif|svg|webp|mp3|wav|ogg|mp4|mov|m4a|flac))(?:\|[^\]]+)?\]\]',
    re.IGNORECASE,
)
ID_PATTERN = re.compile(r'^\s*id::\s*(?P<id>[\w:-]+)', re.IGNORECASE)
IMPORT_ID_PATTERN = re.compile(r'(?<!\w)(?:#card|#long-card)\s+id:([^\s]+)', re.IGNORECASE)
TAGS_PATTERN = re.compile(r'^\s*tags::\s*(?P<tags>.+)$', re.IGNORECASE)
MEDIA_PATTERN = re.compile(r'!\[[^\]]*\]\(([^)]+)\)')
HEADING_PATTERN = re.compile(r'^\s*#+\s*')
HEADING_CAPTURE_PATTERN = re.compile(r'^\s*(?P<hashes>#{1,6})\s*(?P<title>.+)$')


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
    card_type_slug: str
    errors: list[str]
    marker_line: int
    marker_kind: str
    had_explicit_import_id: bool = False
    import_id: str | None = None


@dataclass
class MarkerRule:
    token: str
    card_type_slug: str
    options: dict[str, Any]
    allow_reverse: bool


@dataclass
class MarkerResolver:
    pattern: re.Pattern
    clean_pattern: re.Pattern
    lookup: dict[str, MarkerRule]


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


def _normalise_deck_path(parts: list[str]) -> list[str]:
    cleaned: list[str] = []
    for part in parts:
        if not part:
            continue
        normalised = part.strip()
        lower = normalised.lower()
        if not normalised or lower == 'default':
            continue
        if not cleaned and lower in {'notes'}:
            continue
        cleaned.append(normalised)
    return cleaned


def _strip_root_deck(parts: list[str], root: Deck | None) -> list[str]:
    if not parts or root is None:
        return list(parts)
    root_parts = [segment.strip() for segment in root.full_path().split('/') if segment.strip()]
    lower_parts = [segment.lower() for segment in root_parts]
    index = 0
    for expected in lower_parts:
        if index < len(parts) and parts[index].strip().lower() == expected:
            index += 1
        else:
            break
    if index == 0 and lower_parts:
        root_name = lower_parts[-1]
        if parts and parts[0].strip().lower() == root_name:
            index = 1
    return [part for part in parts[index:]]


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


def _build_marker_resolver(user) -> MarkerResolver:
    from core.services.card_types import ensure_builtin_card_types
    ensure_builtin_card_types()
    
    formats = (
        CardImportFormat.objects.select_related('card_type')
        .filter(format_kind='markdown')
        .filter(Q(card_type__user=user) | Q(card_type__user__isnull=True))
    )
    sorted_formats = sorted(formats, key=lambda fmt: 0 if fmt.card_type and fmt.card_type.user_id == user.id else 1)
    lookup: dict[str, MarkerRule] = {}
    tokens: list[str] = []
    for fmt in sorted_formats:
        card_type = getattr(fmt, 'card_type', None)
        if not card_type or not card_type.slug:
            continue
        options = dict(fmt.options or {})
        markers = options.get('markers')
        if isinstance(markers, str):
            markers = [markers]
        elif isinstance(markers, list):
            markers = [item for item in markers if isinstance(item, str)]
        else:
            marker_value = options.get('marker')
            markers = [marker_value] if isinstance(marker_value, str) else []
        for marker in markers:
            normalized = marker.strip()
            if not normalized:
                continue
            key = normalized.lower()
            if key in lookup:
                continue
            allow_reverse = bool(options.get('allow_reverse', True))
            lookup[key] = MarkerRule(
                token=normalized,
                card_type_slug=card_type.slug,
                options=options,
                allow_reverse=allow_reverse,
            )
            tokens.append(normalized)
    if not lookup:
        default_marker = '#card'
        lookup[default_marker.lower()] = MarkerRule(
            token=default_marker,
            card_type_slug='basic',
            options={'marker': default_marker},
            allow_reverse=True,
        )
        tokens.append(default_marker)
    unique_tokens = sorted({token for token in tokens if token}, key=len, reverse=True)
    if not unique_tokens:
        unique_tokens = ['#card']
    joined = '|'.join(re.escape(token) for token in unique_tokens)
    marker_regex = re.compile(rf'(?<!\w)(?P<marker>{joined})(?P<reverse>(?:[-/]reverse)?)\b', re.IGNORECASE)
    clean_pattern = re.compile(rf'(?<!\w)(?:{joined})(?:[-/]reverse)?', re.IGNORECASE)
    return MarkerResolver(pattern=marker_regex, clean_pattern=clean_pattern, lookup=lookup)


def _ensure_media_directory(user_id: int) -> Path:
    user_media_dir = Path(settings.MEDIA_ROOT) / str(user_id)
    user_media_dir.mkdir(parents=True, exist_ok=True)
    return user_media_dir


def _copy_media(user_id: int, zip_file: zipfile.ZipFile, asset_path: str) -> Tuple[str, dict] | None:
    normalised_asset = asset_path.lstrip('./')
    try:
        data = zip_file.read(normalised_asset)
    except KeyError:
        return None
    digest = hashlib.sha1(data).hexdigest()
    suffix = Path(normalised_asset).suffix or ''
    filename = f"{digest}{suffix}"
    destination_dir = _ensure_media_directory(user_id)
    destination_path = destination_dir / filename
    if not destination_path.exists():
        destination_path.write_bytes(data)
    url = f"{settings.MEDIA_URL}{user_id}/{filename}".replace('//', '/')
    return url, {'name': Path(normalised_asset).name, 'url': url, 'hash': digest}


def _rewrite_media_links(
    text: str, *, user_id: int, zip_file: zipfile.ZipFile, summary: dict, source_dir: str | None = None
) -> Tuple[str, list[dict], list[str]]:
    media_items: list[dict] = []
    missing_assets: list[str] = []

    def _candidate_paths(asset: str) -> list[str]:
        asset_clean = str(Path(asset).as_posix()).lstrip('/').lstrip('./')
        candidates: list[str] = []
        if source_dir:
            base = Path(source_dir)
            direct = (base / asset_clean).as_posix()
            attachments = (base / 'attachments' / asset_clean).as_posix()
            candidates.extend([direct, attachments])
        candidates.append(asset_clean)
        unique: list[str] = []
        for path in candidates:
            normalised = str(Path(path).as_posix()).lstrip('./')
            if normalised and normalised not in unique:
                unique.append(normalised)
        return unique

    def _register(asset: str) -> tuple[str, dict] | None:
        token = (asset or '').strip()
        if re.match(r'^[a-z]+://', token) or token.startswith('data:'):
            return None
        for candidate in _candidate_paths(asset):
            result = _copy_media(user_id, zip_file, candidate)
            if result is None:
                continue
            url, media_meta = result
            if media_meta not in media_items:
                media_items.append(media_meta)
                summary['media_copied'] += 1
            return url, media_meta
        missing_assets.append(asset)
        return None

    def markdown_replacer(match: re.Match) -> str:
        original_path = match.group(1)
        registered = _register(original_path)
        if not registered:
            return match.group(0)
        url, _meta = registered
        return match.group(0).replace(original_path, url)

    def obsidian_replacer(match: re.Match) -> str:
        original_path = match.group('path')
        clean_path = original_path.split('|', 1)[0].strip()
        registered = _register(clean_path)
        if not registered:
            return match.group(0)
        url, _meta = registered
        alt = Path(clean_path).stem or 'image'
        return f'![{alt}]({url})'

    def wiki_media_replacer(match: re.Match) -> str:
        original_path = match.group('path')
        clean_path = original_path.split('|', 1)[0].strip()
        registered = _register(clean_path)
        if not registered:
            return match.group(0)
        url, _meta = registered
        alt = Path(clean_path).stem or 'media'
        return f'![{alt}]({url})'

    rewritten = MEDIA_PATTERN.sub(markdown_replacer, text)
    rewritten = OBSIDIAN_LINK_PATTERN.sub(obsidian_replacer, rewritten)
    rewritten = MEDIA_WIKI_PATTERN.sub(wiki_media_replacer, rewritten)
    return rewritten, media_items, missing_assets


INLINE_MARKER_PATTERNS = [
    re.compile(r'^\*\*(?P<text>.+?)\*\*$'),
    re.compile(r'^__(?P<text>.+?)__$'),
    re.compile(r'^\*(?P<text>.+?)\*$'),
    re.compile(r'^_(?P<text>.+?)_$'),
    re.compile(r'^`(?P<text>.+?)`$'),
]


def _clean_front_text(value: str) -> str:
    value = value.strip()
    value = HEADING_PATTERN.sub('', value)
    value = value.strip()
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
    body = (body or '').strip()
    context = (context or '').strip()
    if context and body:
        return f"{context}\n{body}"
    if context:
        return context
    return body


def _generate_external_key(source_path: str, line_no: int, front_md: str) -> str:
    """Generate a unique external key for a card based on its location and content."""
    # Create a deterministic key based on file path and line number
    key_base = f"{source_path}:{line_no}"
    # Use first 8 chars of hash to keep it reasonably short
    key_hash = hashlib.md5(key_base.encode('utf-8')).hexdigest()[:8]
    return f"md_{key_hash}"


def _build_field_values(parsed: ParsedCard) -> dict:
    hierarchy = (parsed.context_md or '').strip()
    title = (parsed.front_md or '').strip()
    values = {
        'front': title,
        'back': parsed.back_md,
        'hierarchy': hierarchy,
        'title': title,
    }
    if hierarchy:
        values['context'] = hierarchy
    return values


def _update_heading_stack(line: str, stack: list[tuple[int, str]], clean_pattern: re.Pattern) -> int | None:
    match = HEADING_CAPTURE_PATTERN.match(line)
    if not match:
        return None
    stripped_line = line.strip()
    title_only = (match.group('title') or '').strip()
    marker_candidate = f"#{title_only.lstrip('#')}" if title_only else ''
    
    # Skip if this line looks like it contains a card marker (not a real heading)
    if stripped_line and clean_pattern.search(stripped_line):
        return None
    if marker_candidate and clean_pattern.search(marker_candidate):
        return None
    
    level = len(match.group('hashes'))
    title = clean_pattern.sub('', title_only)
    cleaned = _clean_front_text(title)
    if not cleaned:
        return None
    while stack and stack[-1][0] >= level:
        stack.pop()
    stack.append((level, cleaned))
    return level


def _parse_markdown_cards(
    content: str,
    source_path: str,
    *,
    user_id: int,
    zip_file: zipfile.ZipFile,
    summary: dict,
    resolver: MarkerResolver,
    deck_path: list[str] | None = None,
) -> List[ParsedCard]:
    lines = content.splitlines()
    parsed_cards: list[ParsedCard] = []
    deck_parts = _normalise_deck_path(list(deck_path or []))
    i = 0
    heading_stack: list[tuple[int, str]] = []
    while i < len(lines):
        line = lines[i]
        heading_level = _update_heading_stack(line, heading_stack, resolver.clean_pattern)
        marker_match = resolver.pattern.search(line)
        if not marker_match:
            i += 1
            continue

        line_no = i + 1
        marker_start = marker_match.start()
        marker_end = marker_match.end()
        marker_text = (marker_match.group('marker') or '').lower()
        rule = resolver.lookup.get(marker_text)
        allow_reverse = rule.allow_reverse if rule else True
        reverse_flag = bool(marker_match.group('reverse')) and allow_reverse
        rule_options = dict(rule.options) if rule else {}

        front_before = line[:marker_start].strip()
        front_after = line[marker_end:].strip()
        front_content = front_before or front_after
        if front_content:
            front_content = _clean_front_text(front_content)

        # Parse import ID from marker line
        import_id = None
        import_id_match = IMPORT_ID_PATTERN.search(line)
        if import_id_match:
            import_id = import_id_match.group(1).lower()
            # If front_content is just the ID part, remove it
            if front_content and front_content.lower().startswith('id:'):
                front_content = ''
        elif front_after and front_after.lower().startswith('id:'):
            # Handle inline id: syntax without full marker pattern
            id_match = re.match(r'id:([a-f0-9]+)', front_after, re.IGNORECASE)
            if id_match:
                import_id = id_match.group(1).lower()
                front_content = front_before  # Remove the id part from front_content

        if not front_content:
            j = i - 1
            collected: list[str] = []
            found_heading = None
            while j >= 0 and lines[j].strip():
                stripped_line = lines[j].strip()
                heading_match = HEADING_CAPTURE_PATTERN.match(stripped_line)
                if heading_match:
                    # Found a heading - skip it and continue looking back for actual content
                    # (headings are context, not used as front content in lookback)
                    j -= 1
                    continue
                # Skip lines that are pure marker lines (contain a marker at any position)
                # These are previous cards' markers like "#card id:1234" or "Question #card id:1234"
                marker_test = resolver.pattern.search(stripped_line)
                if marker_test:
                    # Any line with a marker is likely a previous card - stop looking back
                    break
                collected.insert(0, stripped_line)
                j -= 1
            
            # Use collected lines if available, otherwise use empty
            if collected:
                front_content = '\n'.join(collected).strip()
            else:
                front_content = ''
            
            front_content = _clean_front_text(front_content)
            if not front_content and heading_stack:
                front_content = heading_stack[-1][1]
        i += 1
        anchor = None
        tags: list[str] = []
        card_errors: list[str] = []
        source_dir_path = Path(source_path).parent
        source_dir = source_dir_path.as_posix()
        if source_dir in {'.', ''}:
            source_dir = None

        if i < len(lines):
            anchor_match = ID_PATTERN.match(lines[i])
            if anchor_match:
                anchor = anchor_match.group('id').strip()
                i += 1

        if i < len(lines):
            tags_match = TAGS_PATTERN.match(lines[i])
            if tags_match:
                tags = _normalise_tags(tags_match.group('tags'))
                i += 1
        if not tags:
            default_tags = rule_options.get('default_tags')
            if isinstance(default_tags, list):
                tags = [str(tag).strip() for tag in default_tags if isinstance(tag, str) and tag.strip()]

        while i < len(lines) and not lines[i].strip():
            i += 1

        back_lines: list[str] = []
        long_card_mode = marker_text == '#long-card' or (rule and rule.token.lower() == '#long-card')
        in_fenced_block = False
        fence_delim = ''
        consecutive_blank = 0

        while i < len(lines):
            candidate = lines[i]
            stripped_line = candidate.strip()

            # Detect fenced code block markers to ignore blank-line termination inside code blocks
            if stripped_line.startswith('```') or stripped_line.startswith('~~~'):
                current_delim = stripped_line[:3]
                if not in_fenced_block:
                    in_fenced_block = True
                    fence_delim = current_delim
                elif current_delim == fence_delim:
                    in_fenced_block = False
                    fence_delim = ''

            # New marker starts next card (unless inside fenced block)
            if not in_fenced_block and resolver.pattern.search(candidate):
                break

            if not long_card_mode:
                if not stripped_line:
                    break
                back_lines.append(candidate)
                i += 1
                continue

            # Long-card mode: allow single blank lines inside back content
            if not stripped_line and not in_fenced_block:
                consecutive_blank += 1
                if consecutive_blank >= 2:
                    i += 1
                    break
                back_lines.append('')
                i += 1
                continue

            if not stripped_line and in_fenced_block:
                back_lines.append(candidate)
                i += 1
                continue

            consecutive_blank = 0
            back_lines.append(candidate)
            i += 1

        back_md_raw = '\n'.join(back_lines).strip()
        if not front_content:
            front_content = _clean_front_text(back_md_raw[:120])
        else:
            front_content = _clean_front_text(front_content)

        if heading_level is not None:
            context_titles = [title for level, title in heading_stack if level < heading_level]
        else:
            context_titles = [title for _level, title in heading_stack]
            if context_titles and context_titles[-1].strip().lower() == front_content.strip().lower():
                context_titles = context_titles[:-1]
        context_md = ' > '.join([title for title in context_titles if title])

        front_md, media_front, missing_front = _rewrite_media_links(
            front_content, user_id=user_id, zip_file=zip_file, summary=summary, source_dir=source_dir
        )
        back_md, media_back, missing_back = _rewrite_media_links(
            back_md_raw, user_id=user_id, zip_file=zip_file, summary=summary, source_dir=source_dir
        )

        suggested_type = rule.card_type_slug if rule else infer_card_type(front_md, back_md)

        media: list[dict] = []
        for item in media_front + media_back:
            if item not in media:
                media.append(item)

        if missing_front or missing_back:
            missing_all = sorted(set(missing_front + missing_back))
            card_errors.append(
                f"Missing attachment(s): {', '.join(missing_all)}. Expected inside an 'attachments' folder next to {source_path}."
            )

        external_key = anchor or _generate_external_key(source_path, line_no, front_md)
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
            card_type_slug=suggested_type,
            errors=list(card_errors),
            marker_line=line_no,
            marker_kind='card',
            had_explicit_import_id=bool(import_id),
            import_id=import_id,
        )
        parsed_cards.append(base_card)
        if reverse_flag:
            reverse_key = f"{external_key}__reverse"
            reverse_type = rule.card_type_slug if rule else infer_card_type(back_md, front_md)
            parsed_cards.append(
                ParsedCard(
                    front_md=back_md,
                    back_md=front_md,
                    context_md=context_md,
                    source_path=source_path,
                    source_anchor=anchor,
                    external_key=reverse_key,
                    tags=tags,
                    media=media,
                    deck_path=list(deck_parts),
                    card_type_slug=reverse_type,
                    errors=list(card_errors),
                    marker_line=line_no,
                    marker_kind='reverse',
                    had_explicit_import_id=bool(import_id),
                    import_id=import_id,
                )
            )
    return parsed_cards


def _collect_markdown_cards(*, user_id: int, uploaded_file, resolver: MarkerResolver) -> Tuple[List[ParsedCard], dict]:
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
            raw_parts = [part for part in Path(info.filename).parts[:-1] if part]
            deck_parts = _normalise_deck_path(raw_parts)
            parsed_cards.extend(
                _parse_markdown_cards(
                    markdown_text,
                    info.filename,
                    user_id=user_id,
                    zip_file=archive,
                    summary=summary,
                    resolver=resolver,
                    deck_path=deck_parts,
                )
            )
    for buffer in buffers:
        buffer.close()
    return parsed_cards, summary


def _normalise_archive_member_path(value: str) -> str:
    normalised = Path(value).as_posix().lstrip('/').lstrip('./')
    if not normalised:
        raise MarkdownImportError('source_path must not be empty')
    return normalised


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


def prepare_markdown_session(*, user, deck: Deck | None, uploaded_file) -> ImportSession:
    resolver = _build_marker_resolver(user)
    parsed_cards, parse_summary = _collect_markdown_cards(
        user_id=user.id, uploaded_file=uploaded_file, resolver=resolver
    )
    
    # Handle import IDs
    import_ids = [card.import_id for card in parsed_cards if card.import_id]
    
    # Check for duplicate import IDs within this session
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
    
    # Generate sequential IDs for cards without explicit IDs
    used_ids = set(Card.objects.filter(import_id__isnull=False).values_list('import_id', flat=True))
    # Include explicit IDs from this import in blocked IDs to avoid conflicts within one batch
    for card in parsed_cards:
        if card.import_id:
            used_ids.add(card.import_id)

    for card in parsed_cards:
        if not card.import_id:
            # Find next available sequential hex ID
            next_id = 1
            while True:
                candidate = hex(next_id)[2:].lower()
                if candidate not in used_ids:
                    card.import_id = candidate
                    used_ids.add(candidate)
                    break
                next_id += 1
        else:
            # Validate the provided ID format
            try:
                int(card.import_id, 16)
            except ValueError:
                card.errors.append(f"Invalid import ID format: '{card.import_id}'. Must be hexadecimal.")
    
    # Detect duplicate import_ids within this import session
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
    source_paths = {card.source_path for card in parsed_cards if getattr(card, 'source_path', None)}
    source_candidates: dict[str, list[Card]] = {}
    if source_paths:
        for candidate in Card.objects.filter(user=user, source_path__in=source_paths).select_related('deck', 'deck__parent'):
            source_candidates.setdefault(candidate.source_path, []).append(candidate)
    consumed_source_ids: set = set()

    for index, parsed in enumerate(parsed_cards):
        parsed.deck_path = _strip_root_deck(_normalise_deck_path(parsed.deck_path), deck)
        if deck is None and not parsed.deck_path:
            parsed.errors.append(
                f"File '{parsed.source_path}' must be inside a folder in the archive when no destination deck is selected."
            )
        card_errors = list(parsed.errors)
        card_warnings = []
        if card_errors:
            has_invalid_cards = True
        external = existing_map.get(parsed.external_key)
        existing_payload = None
        has_changes = False
        fallback_card = None
        incoming_tags = list(parsed.tags)
        display_tags = incoming_tags
        field_values = _build_field_values(parsed)
        hierarchy_value = field_values.get('hierarchy', '')
        title_value = field_values.get('title') or field_values.get('front') or parsed.front_md
        display_front = _compose_front_text(title_value, hierarchy_value)
        display_back = field_values.get('back', parsed.back_md)
        if not external and parsed.source_path:
            candidates = list(source_candidates.get(parsed.source_path, []))
            unused = [candidate for candidate in candidates if candidate.id not in consumed_source_ids]
            if len(unused) == 1:
                fallback_card = unused[0]
            else:
                for candidate in unused:
                    candidate_path = _strip_root_deck(candidate.deck.full_path().split('/'), deck)
                    if candidate_path == parsed.deck_path:
                        fallback_card = candidate
                        break
        existing_card = None
        import_id_conflict = None
        if parsed.import_id in existing_import_id_map:
            import_id_conflict = existing_import_id_map[parsed.import_id]
            if import_id_conflict != existing_card:
                # Different card has this import_id - this is a conflict
                card_warnings.append(f"Import ID '{parsed.import_id}' already exists on a different card. This will update that card.")
        
        if external and external.card.user == user:
            existing_card = external.card
        elif fallback_card:
            existing_card = fallback_card
            consumed_source_ids.add(fallback_card.id)
        elif import_id_conflict:
            # Use the card with the conflicting import_id
            existing_card = import_id_conflict
        if existing_card:
            display_tags = _merge_tags(existing_card.tags, incoming_tags)
            existing_payload = {
                'card_id': str(existing_card.id),
                'deck_id': existing_card.deck_id,
                'deck_path': _strip_root_deck(existing_card.deck.full_path().split('/'), deck),
                'front_md': existing_card.front_md,
                'back_md': existing_card.back_md,
                'tags': existing_card.tags,
                'media': existing_card.media,
                'card_type': existing_card.card_type.slug if existing_card.card_type else 'basic',
                'field_values': existing_card.field_values,
            }
            has_changes = (
                existing_card.front_md != display_front
                or existing_card.back_md != display_back
                or existing_card.tags != display_tags
                or existing_card.media != parsed.media
            )
            if has_changes:
                update_count += 1
                diff_count += 1
            else:
                unchanged_count += 1
        else:
            existing_payload = None
            has_changes = True
            create_count += 1

        diff_payload = None
        if existing_payload and has_changes:
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
                'marker_kind': parsed.marker_kind,
                'had_explicit_import_id': parsed.had_explicit_import_id,
                'deck_path': parsed.deck_path,
                'existing': existing_payload,
                'has_changes': has_changes,
                'unchanged': bool(existing_payload and not has_changes),
                'card_type': parsed.card_type_slug,
                'field_values': field_values,
                'context': parsed.context_md,
                'errors': card_errors,
                'warnings': card_warnings,
                'diff': diff_payload,
            }
        )

    session = ImportSession.objects.create(
        user=user,
        kind='markdown',
        status='ready',
        source_name=getattr(uploaded_file, 'name', ''),
        total=len(session_cards),
        processed=len(session_cards),
        payload={
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
        },
    )
    return session


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
        deck_parts = tuple(_normalise_deck_path(card.get('deck_path', [])))
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
        sample_errors = invalid_cards[0].get('errors') or []
        detail = sample_errors[0] if sample_errors else 'validation errors were found'
        raise MarkdownImportError(f'Resolve the card errors before applying this import. Example: {detail}')

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
                    }
                )
                continue
            deck_parts = _strip_root_deck(_normalise_deck_path(card_data.get('deck_path', [])), root_deck)
            if not deck_parts:
                if not root_deck:
                    raise MarkdownImportError(
                        f"Card '{card_data.get('external_key')}' is missing a folder path. Place the note inside a folder or select a destination deck."
                    )
                target_deck, created_decks = root_deck, []
            else:
                target_deck, created_decks = resolve_deck(deck_parts)
            if created_decks:
                summary['decks_created'] += len(created_decks)

            existing_payload = card_data.get('existing')
            fallback_slug = existing_payload.get('card_type') if isinstance(existing_payload, dict) else None
            card_type_slug = card_data.get('card_type') or fallback_slug or infer_card_type(
                card_data['front_md'], card_data['back_md']
            )
            card_type = resolve_card_type(session.user, card_type_slug)
            field_values = dict(card_data.get('field_values') or {})
            if card_data.get('context') and 'context' not in field_values:
                field_values['context'] = card_data['context']

            if not existing_payload:
                # If an external id already exists, treat this as an update instead of creating a duplicate.
                raw_external_key = card_data['external_key']
                existing_ext = ExternalId.objects.filter(external_key=raw_external_key).select_related('card').first()
                if existing_ext and existing_ext.card and existing_ext.card.user_id == session.user_id:
                    existing_card = existing_ext.card
                    if decision in {'existing', 'skip'}:
                        summary['skipped'] += 1
                        applied_cards.append(
                            {
                                'index': index,
                                'status': 'skipped',
                                'import_id': card_data.get('import_id'),
                                'card_id': str(existing_card.id),
                                'marker_line': card_data.get('marker_line'),
                                'marker_kind': card_data.get('marker_kind'),
                            }
                        )
                        continue
                    basic_types = {'basic', 'basic_image_front', 'basic_image_back'}
                    current_slug = existing_card.card_type.slug if existing_card.card_type else 'basic'
                    if current_slug in basic_types and card_type_slug:
                        existing_card.card_type = resolve_card_type(session.user, card_type_slug)
                    existing_card.front_md = card_data['front_md']
                    existing_card.back_md = card_data['back_md']
                    existing_card.tags = card_data.get('tags', [])
                    existing_card.media = card_data.get('media', [])
                    existing_card.source_path = card_data.get('source_path')
                    existing_card.source_anchor = card_data.get('source_anchor')
                    existing_card.deck = target_deck
                    existing_card.field_values = field_values
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
                            'card_type',
                            'field_values',
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
                        }
                    )
                    continue
                if decision in {'existing', 'skip'}:
                    summary['skipped'] += 1
                    applied_cards.append(
                        {
                            'index': index,
                            'status': 'skipped',
                            'import_id': card_data.get('import_id'),
                            'card_id': None,
                            'marker_line': card_data.get('marker_line'),
                            'marker_kind': card_data.get('marker_kind'),
                        }
                    )
                    continue
                external_key = raw_external_key
                if existing_ext and existing_ext.card and existing_ext.card.user_id != session.user_id:
                    # Another user already owns this external id; generate a unique, user-scoped key to avoid collision.
                    external_key = _unique_external_key(f"{raw_external_key}__u{session.user_id}")
                card = Card.objects.create(
                    user=session.user,
                    deck=target_deck,
                    card_type=card_type,
                    front_md=card_data['front_md'],
                    back_md=card_data['back_md'],
                    tags=card_data.get('tags', []),
                    media=card_data.get('media', []),
                    source_path=card_data.get('source_path'),
                    source_anchor=card_data.get('source_anchor'),
                    field_values=field_values,
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
                    }
                )
                continue

            try:
                existing_card = Card.objects.get(id=existing_payload['card_id'], user=session.user)
            except (KeyError, Card.DoesNotExist):
                # Card disappeared; treat as new card.
                if decision in {'existing', 'skip'}:
                    summary['skipped'] += 1
                    applied_cards.append(
                        {
                            'index': index,
                            'status': 'skipped',
                            'import_id': card_data.get('import_id'),
                            'card_id': None,
                            'marker_line': card_data.get('marker_line'),
                            'marker_kind': card_data.get('marker_kind'),
                        }
                    )
                    continue
                card = Card.objects.create(
                    user=session.user,
                    deck=target_deck,
                    card_type=card_type,
                    front_md=card_data['front_md'],
                    back_md=card_data['back_md'],
                    tags=card_data.get('tags', []),
                    media=card_data.get('media', []),
                    source_path=card_data.get('source_path'),
                    source_anchor=card_data.get('source_anchor'),
                    field_values=field_values,
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
                    }
                )
                continue

            if decision in {'existing', 'skip'}:
                summary['skipped'] += 1
                applied_cards.append(
                    {
                        'index': index,
                        'status': 'skipped',
                        'import_id': existing_card.import_id,
                        'card_id': str(existing_card.id),
                        'marker_line': card_data.get('marker_line'),
                        'marker_kind': card_data.get('marker_kind'),
                    }
                )
                continue

            basic_types = {'basic', 'basic_image_front', 'basic_image_back'}
            current_slug = existing_card.card_type.slug if existing_card.card_type else 'basic'
            if current_slug in basic_types and card_type_slug:
                existing_card.card_type = resolve_card_type(session.user, card_type_slug)
            existing_card.front_md = card_data['front_md']
            existing_card.back_md = card_data['back_md']
            existing_card.tags = card_data.get('tags', [])
            existing_card.media = card_data.get('media', [])
            existing_card.source_path = card_data.get('source_path')
            existing_card.source_anchor = card_data.get('source_anchor')
            existing_card.deck = target_deck
            existing_card.field_values = field_values
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
                    'card_type',
                    'field_values',
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
                }
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
