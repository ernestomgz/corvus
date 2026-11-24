import pytest

from core.services.markdown_renderer import render_to_html


@pytest.mark.parametrize(
    ('source', 'expected_class', 'expected_tokens'),
    [
        ('\\(x = \\frac{1}{2}\\)', 'math-inline', ('\\(', '\\)')),
        ('[]y = x^2[/]', 'math-block', ('\\[', '\\]')),
        ('$value$', 'math-inline', ('\\(', '\\)')),
        ('$$E = mc^2$$', 'math-block', ('\\[', '\\]')),
    ],
)
def test_math_rendering_is_mathjax_ready(source, expected_class, expected_tokens):
    html = render_to_html(source)
    assert expected_class in html
    for token in expected_tokens:
        assert token in html
    assert 'math' in html


def test_multiple_math_segments_preserve_each_expression():
    d = '$'
    source = f'{d}{d}A{d}{d} and {d}b{d} plus \\(c\\)'
    html = render_to_html(source)
    assert html.count('math-inline') >= 2
    assert 'math-block' in html
    assert html.count('\\(') >= 2
    assert '\\[' in html


def test_html_wrapping_does_not_leave_stray_delimiters():
    source = '<p>\\[z = r(\\cos\\theta + i\\sin\\theta)\\] where \\(r = |z|\\)</p>'
    html = render_to_html(source)
    assert html.count('math math-block') == 1
    assert html.count('math math-inline') >= 1
    assert '</span>\\' not in html
    assert '</div>\\' not in html


def test_render_sanitises_script_tags():
    html = render_to_html('<script>alert(1)</script><p>Safe</p>')
    assert 'alert(1)' not in html
    assert '<p>Safe</p>' in html


@pytest.mark.parametrize('source', [
    '<script type="text/tikz">Discard me</script>',
    '<ScRiPt title="a > b">Discard me</ScRiPt>',
    '<script>Discard me',
    '<svg><script>Discard me</script></svg>',
])
def test_render_drops_script_elements_and_their_contents(source):
    rendered = render_to_html(f'<p>Keep me</p>\n\n{source}')
    assert '<p>Keep me</p>' in rendered
    assert 'Discard me' not in rendered
    assert '<script' not in rendered.lower()


def test_render_preserves_script_like_text_and_math_in_fenced_code():
    rendered = render_to_html('```text\n<script>literal text</script>\n$x^2$\n```\n\n$y^2$')
    assert '&lt;script&gt;literal text&lt;/script&gt;' in rendered
    assert '$x^2$' in rendered
    assert rendered.count('math-inline') == 1


@pytest.mark.parametrize('language', ['tikz', 'latex'])
def test_render_marks_legacy_tikz_as_inert_code(language):
    rendered = render_to_html(
        f'```{language}\n\\begin{{tikzpicture}}\n% </script>\n\\node {{$x$}};\n\\end{{tikzpicture}}\n```'
    )
    assert f'<pre data-corvus-tikz><code class="language-{language}">' in rendered
    assert '&lt;/script&gt;' in rendered
    assert '$x$' in rendered
    assert '<script' not in rendered


def test_render_strips_submitted_tikz_marker_attributes():
    rendered = render_to_html('<pre data-corvus-tikz><code>Plain text</code></pre>')
    assert rendered.strip() == '<pre><code>Plain text</code></pre>'


@pytest.mark.parametrize('language', ['latex', 'tikz-example'])
def test_render_keeps_unrelated_fences_as_code(language):
    rendered = render_to_html(f'```{language}\n\\frac{{1}}{{2}}\n```')
    assert 'data-corvus-tikz' not in rendered
    assert f'class="language-{language}"' in rendered


def test_render_handles_plain_markdown():
    html = render_to_html('# Title\n\n*Item*')
    assert '<h1>' in html
    assert '<em>Item</em>' in html


def test_inline_code_preserves_math_wiki_links_and_example_images():
    html = render_to_html('`$x$` and ``[[Example|Label]] ` ![example](a.png)`` and $y$.')
    assert '<code>$x$</code>' in html
    assert '<code>[[Example|Label]] ` ![example](a.png)</code>' in html
    assert html.count('math-inline') == 1
    assert '<img' not in html


def test_ordered_list_preserves_start_and_nested_list_structure():
    html = render_to_html('3. Check the current.\n   - Compare it with the expected value.\n4. Check the voltage.')
    assert '<ol start="3">' in html
    assert '<li>Check the current.\n<ul>' in html
    assert '<li>Check the voltage.</li>' in html
