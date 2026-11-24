"""Source ranges for syntax that card import and rendering must leave literal."""

import re
from collections.abc import Iterator

from markdown_it import MarkdownIt


_markdown = MarkdownIt('commonmark')
_backticks = re.compile(r'`+')
_paragraph_break = re.compile(r'\n[ \t]*\n')
_ordered_item = re.compile(r'^( {0,3})([0-9]{1,9})([.)])[ \t]+\S')


def is_escaped(text: str, start: int) -> bool:
    slashes = 0
    while start > 0 and text[start - 1] == '\\':
        slashes += 1
        start -= 1
    return slashes % 2 == 1


def code_block_lines(text: str) -> set[int]:
    return {
        line
        for token in _markdown.parse(text)
        if token.type in {'fence', 'code_block'} and token.map is not None
        for line in range(*token.map)
    }


def inline_code_ranges(text: str) -> Iterator[tuple[int, int]]:
    runs = list(_backticks.finditer(text))
    next_same = {}
    closing_runs = [None] * len(runs)
    for index in range(len(runs) - 1, -1, -1):
        size = len(runs[index][0])
        closing_runs[index] = next_same.get(size - is_escaped(text, runs[index].start()))
        next_same[size] = index

    index = 0
    while index < len(runs):
        start = runs[index].start() + is_escaped(text, runs[index].start())
        close = closing_runs[index]
        if close is not None and not _paragraph_break.search(text, runs[index].end(), runs[close].start()):
            yield start, runs[close].end()
            index = close + 1
        else:
            index += 1


def literal_code_ranges(text: str, block_lines: set[int] | None = None) -> Iterator[tuple[int, int]]:
    if block_lines is None:
        block_lines = code_block_lines(text)
    offset = plain_start = 0
    for index, line in enumerate(text.splitlines(keepends=True)):
        if index in block_lines:
            if plain_start < offset:
                for start, end in inline_code_ranges(text[plain_start:offset]):
                    yield plain_start + start, plain_start + end
            yield offset, offset + len(line)
            plain_start = offset + len(line)
        offset += len(line)
    if plain_start < len(text):
        for start, end in inline_code_ranges(text[plain_start:]):
            yield plain_start + start, plain_start + end


def code_mask(text: str, block_lines: set[int] | None = None) -> bytearray:
    mask = bytearray(len(text))
    for start, end in literal_code_ranges(text, block_lines):
        mask[start:end] = b'\1' * (end - start)
    return mask


def separate_numbered_lists(text: str) -> str:
    """Recognize a numbered sequence after a prompt even if it starts above one."""
    if not re.search(r'\n {0,3}[0-9]+[.)][ \t]', text):
        return text
    lines = text.split('\n')
    breaks = set()
    for token in _markdown.parse(text):
        if token.type != 'paragraph_open' or token.level or token.map is None:
            continue
        start, end = token.map
        for index in range(start + 1, end):
            match = _ordered_item.match(lines[index])
            if not match:
                continue
            indent, number, delimiter = match.groups()
            next_index = index + 1
            continuation = indent + ' ' * (len(number) + 2)
            while next_index < len(lines) and lines[next_index].startswith(continuation) and lines[next_index].strip():
                next_index += 1
            next_item = _ordered_item.match(lines[next_index]) if next_index < len(lines) else None
            if next_item and next_item[1] == indent and int(next_item[2]) == int(number) + 1 and next_item[3] == delimiter:
                breaks.add(index)
                break
    for index in sorted(breaks, reverse=True):
        lines.insert(index, '')
    return '\n'.join(lines)
