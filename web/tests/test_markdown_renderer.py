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


def test_render_handles_plain_markdown():
    html = render_to_html('# Title\n\n*Item*')
    assert '<h1>' in html
    assert '<em>Item</em>' in html
