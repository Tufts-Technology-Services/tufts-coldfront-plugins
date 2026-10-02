# SPDX-FileCopyrightText: (C) 2026 Tufts Technology Services (TTS)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Checks that apply to every template in the plugin, regardless of which view renders it."""

import pathlib
import re

import pytest

TEMPLATE_ROOT = pathlib.Path(__file__).resolve().parent.parent / 'templates'
TEMPLATES = sorted(TEMPLATE_ROOT.rglob('*.html'))

# Markup Bootstrap 5 renamed or dropped. Each one fails silently: the page still renders and
# every `x in content` assertion still passes, while the popover never opens, the badge loses
# its colour or the checkbox loses its styling. Only a sweep like this one catches them.
BOOTSTRAP4_MARKUP = (
    r'data-(?:toggle|target|dismiss|content|trigger|html)=',
    r'\bbadge-(?:success|danger|warning|info|secondary|primary|light|dark)\b',
    r'\bfloat-(?:right|left)\b',
    r'\b[mp](?:r|l)-[0-5]\b',
    r'\bform-(?:group|inline)\b',
    r'\bcustom-(?:control|checkbox|select|radio|switch|file|range)',
    r'class="close"',
    r'\.popover\(',
)


def test_templates_were_found():
    """Guards the guard: an rglob that matched nothing would make every check below vacuous."""
    assert TEMPLATES


@pytest.mark.parametrize('template', TEMPLATES, ids=lambda p: p.name)
def test_hash_comments_stay_on_one_line(template):
    """A {# #} comment split across lines is emitted verbatim into the page.

    Django lexes comments with `{#.*?#}` and no re.DOTALL, so a newline inside one means
    it never matches and the text renders as content. Nothing else catches it: the page
    still renders, and tests asserting `x in content` all still pass because the leaked
    comment is additive. Use {% comment %}...{% endcomment %} when it needs more than a line.
    """
    unterminated = [
        (number, line.strip())
        for number, line in enumerate(template.read_text().splitlines(), start=1)
        if '{#' in line and '#}' not in line.split('{#', 1)[1]
    ]

    assert not unterminated, f'{template.name}: multiline {{# #}} comment at {unterminated}'


@pytest.mark.parametrize('template', TEMPLATES, ids=lambda p: p.name)
def test_no_bootstrap4_markup(template):
    """coldfront 1.1.9 ships Bootstrap 5, which ignores Bootstrap 4's data-* attributes and
    dropped its badge-*, float-right, form-group and custom-control classes.

    Nothing else catches these. The page renders either way, so every assertion about its
    content still passes while the popover stays shut and the badge comes out grey.
    """
    text = template.read_text()
    found = [
        (number, pattern)
        for pattern in BOOTSTRAP4_MARKUP
        for number, line in enumerate(text.splitlines(), start=1)
        if re.search(pattern, line)
    ]

    assert not found, f'{template.name}: Bootstrap 4 markup at {found}'
