# SPDX-FileCopyrightText: (C) 2026 Tufts Technology Services (TTS)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
What this ColdFront is running: its version, Python and Django's, and the installed packages
that provide the enabled apps, each with its version and where it was installed from.

Everything is read from the installed packages' metadata. A package installed from git has a
`direct_url.json` (PEP 610) recording the commit, which for Tufts' hosts is the commit the fork's
`uv.lock` names. The URL in that file can carry credentials, so it is never kept.
"""

import json
import platform
from dataclasses import dataclass
from importlib.metadata import distribution, packages_distributions

import django
from django.apps import apps


@dataclass(frozen=True)
class InstallSource:
    """
    Where a package was installed from, without the URL: that can carry credentials.

    `kind` is "git", "editable", "directory", "index" or "unknown". Only a git install has a
    commit, and a revision is the branch or tag it was asked for, where there was one.
    """

    kind: str
    commit: str | None = None
    revision: str | None = None


@dataclass(frozen=True)
class PackageInfo:
    """An installed package, and the enabled Django apps it provides."""

    name: str
    version: str
    source: InstallSource
    apps: tuple[str, ...]


@dataclass(frozen=True)
class SoftwareVersions:
    python_version: str
    django_version: str
    coldfront: PackageInfo | None
    packages: list[PackageInfo]
    unpackaged_apps: list[str]


def describe_install_source(direct_url_text: str | None) -> InstallSource:
    """
    Read a package's `direct_url.json` text (None where it has none) into an InstallSource.

    No file means it came from an index. A file that can't be read, or that isn't one of the
    shapes PEP 610 defines, is "unknown", not an error: this feeds a page, not a decision.
    """
    if direct_url_text is None:
        return InstallSource("index")
    try:
        record = json.loads(direct_url_text)
    except ValueError:
        return InstallSource("unknown")
    if not isinstance(record, dict):
        return InstallSource("unknown")

    vcs_info = record.get("vcs_info")
    if isinstance(vcs_info, dict) and vcs_info.get("vcs") == "git" and vcs_info.get("commit_id"):
        return InstallSource("git", commit=str(vcs_info["commit_id"]), revision=vcs_info.get("requested_revision"))

    dir_info = record.get("dir_info")
    if isinstance(dir_info, dict):
        return InstallSource("editable" if dir_info.get("editable") else "directory")
    return InstallSource("unknown")


def get_software_versions() -> SoftwareVersions:
    """
    Gather what the Software Versions page shows.

    The enabled apps are grouped under the distribution that provides their top-level module.
    ColdFront's own distribution is `coldfront`, apart from `packages`; enabled apps that no
    installed distribution provides are `unpackaged_apps`. `packages` is sorted by name, ignoring
    case.
    """
    top_level_modules = packages_distributions()
    apps_by_distribution = {}
    unpackaged_apps = []
    for config in apps.get_app_configs():
        providers = top_level_modules.get(config.name.partition(".")[0])
        if providers:
            # a top-level module several distributions claim goes to the first by name, so it's stable
            apps_by_distribution.setdefault(sorted(providers)[0], []).append(config.name)
        else:
            unpackaged_apps.append(config.name)

    packages = []
    for distribution_name, app_names in apps_by_distribution.items():
        installed = distribution(distribution_name)
        packages.append(
            PackageInfo(
                name=installed.metadata["Name"],
                version=installed.version,
                source=describe_install_source(installed.read_text("direct_url.json")),
                apps=tuple(sorted(app_names)),
            )
        )
    packages.sort(key=lambda package: package.name.lower())

    coldfront = next((package for package in packages if package.name.lower() == "coldfront"), None)
    return SoftwareVersions(
        python_version=platform.python_version(),
        django_version=django.get_version(),
        coldfront=coldfront,
        packages=[package for package in packages if package is not coldfront],
        unpackaged_apps=sorted(unpackaged_apps),
    )
