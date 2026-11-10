# SPDX-FileCopyrightText: (C) 2026 Tufts Technology Services (TTS)
#
# SPDX-License-Identifier: GPL-3.0-or-later

import json
import platform

import django
import pytest
from django.apps import apps
from django.contrib.auth import get_user_model

from tufts_local.software_versions import describe_install_source, get_software_versions

COMMIT = "0123456789abcdef0123456789abcdef01234567"

# a placeholder, not a real credential: the point is that nothing of the URL is kept or shown
URL_WITH_EXAMPLE_CREDENTIALS = "https://EXAMPLE_USER:EXAMPLE_TOKEN@github.com/example/repo.git"


def git_direct_url(commit=COMMIT, revision="tufts-dev", url="https://github.com/example/repo.git"):
    return json.dumps({"url": url, "vcs_info": {"vcs": "git", "commit_id": commit, "requested_revision": revision}})


class FakeDistribution:
    """Enough of importlib.metadata.Distribution for software_versions.py: a name, a version, direct_url.json."""

    def __init__(self, name, version, direct_url=None):
        self.metadata = {"Name": name}
        self.version = version
        self._direct_url = direct_url

    def read_text(self, filename):
        return self._direct_url if filename == "direct_url.json" else None


@pytest.fixture
def installed_packages(monkeypatch):
    """
    Pretend these distributions are installed, and provide these top-level modules.

    Returns a function taking {top_level_module: FakeDistribution}.
    """

    def install(modules_to_distributions):
        by_name = {dist.metadata["Name"]: dist for dist in modules_to_distributions.values()}
        monkeypatch.setattr(
            "tufts_local.software_versions.packages_distributions",
            lambda: {module: [dist.metadata["Name"]] for module, dist in modules_to_distributions.items()},
        )
        monkeypatch.setattr("tufts_local.software_versions.distribution", lambda name: by_name[name])

    return install


class TestDescribeInstallSource:
    def test_a_git_install_has_its_commit_and_requested_revision(self):
        source = describe_install_source(git_direct_url())

        assert source.kind == "git"
        assert source.commit == COMMIT
        assert source.revision == "tufts-dev"

    def test_a_git_install_without_a_requested_revision_has_none(self):
        source = describe_install_source(
            json.dumps({"url": "https://github.com/example/repo.git", "vcs_info": {"vcs": "git", "commit_id": COMMIT}})
        )

        assert source.kind == "git"
        assert source.revision is None

    def test_never_keeps_the_url_which_can_carry_credentials(self):
        source = describe_install_source(git_direct_url(url=URL_WITH_EXAMPLE_CREDENTIALS))

        assert "EXAMPLE_TOKEN" not in repr(source)

    def test_an_editable_install_has_no_commit(self):
        source = describe_install_source(json.dumps({"url": "file:///src/repo", "dir_info": {"editable": True}}))

        assert source.kind == "editable"
        assert source.commit is None

    def test_a_local_directory_install_has_no_commit(self):
        source = describe_install_source(json.dumps({"url": "file:///src/repo", "dir_info": {}}))

        assert source.kind == "directory"
        assert source.commit is None

    def test_a_package_with_no_direct_url_came_from_an_index(self):
        source = describe_install_source(None)

        assert source.kind == "index"
        assert source.commit is None

    @pytest.mark.parametrize("text", ["not json", "[]", "{}", json.dumps({"url": "https://example.com/x.whl"})])
    def test_anything_it_cannot_read_is_unknown_not_an_error(self, text):
        source = describe_install_source(text)

        assert source.kind == "unknown"
        assert source.commit is None


@pytest.mark.django_db
class TestGetSoftwareVersions:
    def test_reports_the_python_and_django_versions(self):
        info = get_software_versions()

        assert info.python_version == platform.python_version()
        assert info.django_version == django.get_version()

    def test_reports_the_coldfront_version_and_commit_apart_from_the_other_packages(self, installed_packages):
        installed_packages(
            {
                "coldfront": FakeDistribution("coldfront", "1.1.10", git_direct_url()),
                "storage": FakeDistribution("coldfront-storage-plugin", "0.4.0"),
            }
        )

        info = get_software_versions()

        assert info.coldfront.name == "coldfront"
        assert info.coldfront.version == "1.1.10"
        assert info.coldfront.source.commit == COMMIT
        assert "coldfront" not in [package.name for package in info.packages]

    def test_groups_the_enabled_apps_under_the_package_that_provides_them(self, installed_packages):
        installed_packages(
            {
                "django": FakeDistribution("Django", "5.2"),
                "coldfront": FakeDistribution("coldfront", "1.1.10"),
            }
        )

        info = get_software_versions()

        [django_package] = [package for package in info.packages if package.name == "Django"]
        assert "django.contrib.auth" in django_package.apps
        assert "django.contrib.admin" in django_package.apps
        assert "coldfront.core.project" in info.coldfront.apps

    def test_lists_a_plugin_with_its_version_and_install_source(self, installed_packages):
        installed_packages(
            {
                "coldfront": FakeDistribution("coldfront", "1.1.10"),
                "tufts_local": FakeDistribution("tufts-coldfront-plugins", "0.2.0", git_direct_url("a" * 40)),
            }
        )

        [plugin] = [
            package for package in get_software_versions().packages if package.name == "tufts-coldfront-plugins"
        ]

        assert plugin.version == "0.2.0"
        assert plugin.source.commit == "a" * 40
        assert plugin.apps == ("tufts_local",)

    def test_names_enabled_apps_that_no_installed_package_provides(self, installed_packages):
        installed_packages({"coldfront": FakeDistribution("coldfront", "1.1.10")})

        info = get_software_versions()

        assert "tufts_local" in info.unpackaged_apps
        assert "coldfront.core.project" not in info.unpackaged_apps

    def test_sorts_the_packages_by_name_ignoring_case(self, installed_packages):
        installed_packages(
            {
                "django": FakeDistribution("Django", "5.2"),
                "crispy_forms": FakeDistribution("crispy-forms", "2.0"),
                "coldfront": FakeDistribution("coldfront", "1.1.10"),
            }
        )

        names = [package.name for package in get_software_versions().packages]

        assert names == sorted(names, key=str.lower)

    def test_runs_against_the_real_environment_and_lists_every_enabled_app_once(self):
        info = get_software_versions()

        assert info.coldfront is not None
        assert info.coldfront.version
        listed = [app for package in [info.coldfront, *info.packages] for app in package.apps]
        listed += info.unpackaged_apps
        # where it lands depends on how this checkout is installed: an editable install of
        # tufts_local has no module listing for the metadata to map, so it's unpackaged
        assert sorted(listed) == sorted(config.name for config in apps.get_app_configs())


def make_account(username, **fields):
    return get_user_model().objects.create(username=username, **fields)


@pytest.fixture
def staff_client(client):
    client.force_login(make_account("staff01", is_staff=True))
    return client


@pytest.mark.django_db
@pytest.mark.urls("tufts_local.tests.urls")
class TestSoftwareVersionsPage:
    def test_shows_the_python_django_and_coldfront_versions(self, staff_client, installed_packages):
        installed_packages({"coldfront": FakeDistribution("coldfront", "1.1.10")})

        response = staff_client.get("/software-versions/")

        assert response.status_code == 200
        page = response.content.decode()
        assert platform.python_version() in page
        assert django.get_version() in page
        assert "1.1.10" in page

    def test_shows_each_plugin_with_its_version_and_the_short_commit(self, staff_client, installed_packages):
        installed_packages(
            {
                "coldfront": FakeDistribution("coldfront", "1.1.10", git_direct_url("b" * 40)),
                "tufts_local": FakeDistribution("tufts-coldfront-plugins", "0.2.0", git_direct_url(COMMIT)),
            }
        )

        page = staff_client.get("/software-versions/").content.decode()

        assert "tufts-coldfront-plugins" in page
        assert "0.2.0" in page
        assert COMMIT[:7] in page
        assert "b" * 7 in page
        # the full hash is there to copy
        assert COMMIT in page

    def test_says_when_a_package_has_no_commit(self, staff_client, installed_packages):
        installed_packages(
            {
                "coldfront": FakeDistribution("coldfront", "1.1.10"),
                "tufts_local": FakeDistribution(
                    "tufts-coldfront-plugins",
                    "0.2.0",
                    json.dumps({"url": "file:///src/repo", "dir_info": {"editable": True}}),
                ),
            }
        )

        page = staff_client.get("/software-versions/").content.decode()

        assert "editable install" in page

    def test_never_shows_a_url_from_the_install_record(self, staff_client, installed_packages):
        installed_packages(
            {"coldfront": FakeDistribution("coldfront", "1.1.10", git_direct_url(url=URL_WITH_EXAMPLE_CREDENTIALS))}
        )

        page = staff_client.get("/software-versions/").content.decode()

        assert "EXAMPLE_TOKEN" not in page

    def test_lists_the_enabled_apps_of_a_package(self, staff_client, installed_packages):
        installed_packages({"coldfront": FakeDistribution("coldfront", "1.1.10")})

        page = staff_client.get("/software-versions/").content.decode()

        assert "coldfront.core.project" in page

    def test_superusers_may_see_it_even_without_the_staff_flag(self, client, installed_packages):
        installed_packages({"coldfront": FakeDistribution("coldfront", "1.1.10")})
        client.force_login(make_account("rdms_admin", is_superuser=True))

        assert client.get("/software-versions/").status_code == 200

    def test_signed_in_users_who_are_not_staff_may_not(self, client):
        client.force_login(make_account("alice"))

        assert client.get("/software-versions/").status_code == 302

    def test_anonymous_users_may_not(self, client):
        assert client.get("/software-versions/").status_code == 302

    def test_only_get_is_allowed(self, staff_client):
        assert staff_client.post("/software-versions/").status_code == 405
