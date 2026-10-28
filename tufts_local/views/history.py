# SPDX-FileCopyrightText: (C) 2026 Tufts Technology Services (TTS)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
The project change history: a search to find the project, then its timeline. Superusers only,
reached from Reporting -> Project History.
"""

from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import Http404
from django.template.response import TemplateResponse
from django.views.decorators.http import require_GET

from coldfront.core.project.models import Project

from tufts_local.history import get_project_membership_changes

CHANGES_PER_PAGE = 50
# a search matching more than this is too broad to list; the page asks for something narrower
MAX_SEARCH_RESULTS = 100


def _is_superuser(user):
    return user.is_superuser


@login_required
@require_GET
@user_passes_test(_is_superuser)
def project_history_search(request):
    """
    Find a project by title, PI username or Project Key, to open its history.

    Every status is included, archived ones too: a project's history outlives its status.
    """
    query = request.GET.get('q', '').strip()
    projects = []
    too_many = False
    if query:
        matches = (
            Project.objects.filter(
                Q(title__icontains=query)
                | Q(pi__username__icontains=query)
                | Q(projectattribute__proj_attr_type__name='Project Key', projectattribute__value__icontains=query)
            )
            .select_related('pi', 'status')
            .distinct()
            .order_by('title')
        )
        projects = list(matches[: MAX_SEARCH_RESULTS + 1])
        too_many = len(projects) > MAX_SEARCH_RESULTS
        projects = projects[:MAX_SEARCH_RESULTS]
    return TemplateResponse(
        request,
        'tufts_local/project_history_search.html',
        {'query': query, 'projects': projects, 'too_many': too_many, 'max_results': MAX_SEARCH_RESULTS},
    )


@login_required
@require_GET
@user_passes_test(_is_superuser)
def project_history(request, pk):
    """
    One project's change history, newest first, CHANGES_PER_PAGE to a page.

    For now that is its members' roles and statuses (tufts_local.history.get_project_membership_changes).
    The plan's first iteration adds the project's own fields and attributes, and its allocations.
    """
    try:
        project = Project.objects.select_related('pi', 'status').get(pk=pk)
    except Project.DoesNotExist:
        raise Http404('No such project') from None
    paginator = Paginator(get_project_membership_changes(project.pk), CHANGES_PER_PAGE)
    page_obj = paginator.get_page(request.GET.get('page'))
    return TemplateResponse(
        request,
        'tufts_local/project_history.html',
        {'project': project, 'page_obj': page_obj, 'is_paginated': paginator.num_pages > 1},
    )
