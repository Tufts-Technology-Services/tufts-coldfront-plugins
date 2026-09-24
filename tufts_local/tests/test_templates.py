# SPDX-FileCopyrightText: (C) 2026 Tufts Technology Services (TTS)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Checks that apply to every template in the plugin, regardless of which view renders it."""

import pathlib

import pytest

TEMPLATE_ROOT = pathlib.Path(__file__).resolve().parent.parent / 'templates'
TEMPLATES = sorted(TEMPLATE_ROOT.rglob('*.html'))


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
