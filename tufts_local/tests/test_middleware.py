# SPDX-FileCopyrightText: (C) 2026 Tufts Technology Services (TTS)
#
# SPDX-License-Identifier: GPL-3.0-or-later

import pytest
from django.conf import settings
from django.http import HttpResponse, HttpResponsePermanentRedirect, HttpResponseRedirect
from django.shortcuts import resolve_url
from django.test import RequestFactory
from django_htmx.middleware import HtmxMiddleware

from tufts_local.middleware import HtmxLoginRefreshMiddleware

MIDDLEWARE_PATH = 'tufts_local.middleware.HtmxLoginRefreshMiddleware'


@pytest.fixture
def login_path():
    return resolve_url(settings.LOGIN_URL)


def respond(response, htmx=True):
    """send a request through HtmxMiddleware and ours, to a view that answers with response"""
    headers = {'HTTP_HX_REQUEST': 'true'} if htmx else {}
    request = RequestFactory().get('/fragment/', **headers)
    return HtmxMiddleware(HtmxLoginRefreshMiddleware(lambda request: response))(request)


class TestHtmxLoginRefresh:
    def test_htmx_redirect_to_login_becomes_a_refresh(self, login_path):
        response = respond(HttpResponseRedirect(f'{login_path}?next=/fragment/'))

        assert response.status_code == 200
        assert response['HX-Refresh'] == 'true'
        assert 'Location' not in response

    def test_absolute_login_url_too(self, login_path):
        response = respond(HttpResponseRedirect(f'https://coldfront.example.edu{login_path}?next=/fragment/'))

        assert response['HX-Refresh'] == 'true'

    @pytest.mark.parametrize('status', [301, 303, 307, 308])
    def test_every_redirect_status(self, login_path, status):
        redirect = HttpResponseRedirect(login_path)
        redirect.status_code = status

        assert respond(redirect)['HX-Refresh'] == 'true'

    def test_permanent_redirect_class(self, login_path):
        assert respond(HttpResponsePermanentRedirect(login_path))['HX-Refresh'] == 'true'

    def test_non_htmx_request_keeps_its_redirect(self, login_path):
        redirect = HttpResponseRedirect(f'{login_path}?next=/fragment/')

        assert respond(redirect, htmx=False) is redirect

    @pytest.mark.parametrize('location', ['/project/', '/user/login-help/', 'https://idp.example.edu/sso'])
    def test_htmx_redirect_elsewhere_is_kept(self, location):
        redirect = HttpResponseRedirect(location)

        assert respond(redirect) is redirect

    def test_htmx_ordinary_response_is_kept(self):
        page = HttpResponse('fragment')

        assert respond(page) is page

    def test_without_htmx_middleware_nothing_changes(self, login_path):
        # request.htmx unset: a misordered MIDDLEWARE mustn't break requests
        redirect = HttpResponseRedirect(login_path)
        request = RequestFactory().get('/fragment/', HTTP_HX_REQUEST='true')

        assert HtmxLoginRefreshMiddleware(lambda request: redirect)(request) is redirect


@pytest.mark.django_db
@pytest.mark.urls('tufts_local.tests.urls')
class TestWithColdfrontMiddleware:
    """The real stack and a real login-protected view: the task report, as an anonymous user."""

    @pytest.fixture(autouse=True)
    def with_middleware(self, settings):
        settings.MIDDLEWARE = [*settings.MIDDLEWARE, MIDDLEWARE_PATH]

    def test_expired_session_htmx_request_refreshes(self, client):
        response = client.get('/task-report/', HTTP_HX_REQUEST='true')

        assert response.status_code == 200
        assert response['HX-Refresh'] == 'true'

    def test_page_request_still_redirects_to_login_with_next(self, client, login_path):
        response = client.get('/task-report/')

        assert response.status_code == 302
        assert response['Location'].startswith(login_path)
        assert 'next=/task-report/' in response['Location']
