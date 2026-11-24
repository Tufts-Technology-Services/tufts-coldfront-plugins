# SPDX-FileCopyrightText: (C) 2026 Tufts Technology Services (TTS)
#
# SPDX-License-Identifier: GPL-3.0-or-later

from coldfront_utils.task_context import acting_as
from django.core.management.base import BaseCommand, CommandError

from tufts_local.legacy_import import (
    apply_plan,
    describe_plan,
    plan_project,
    read_legacy_csv,
    starfish_tags_by_vol_path,
)
from tufts_local.tasks import approver_vol_paths_by_project


class Command(BaseCommand):
    help = (
        "Set legacy projects' users_managed and Group attributes from a CSV (Name, Project ID, Project Key, "
        "user_managed, Group, Users), and add the Users who are in AD, with roles from Starfish's Approver "
        "tags, as project and allocation users and to the Grouper group. Validates every row first; with "
        "--dry-run, only validates and shows what would change."
    )

    def add_arguments(self, parser):
        parser.add_argument("csv_path")
        parser.add_argument("--dry-run", action="store_true", help="Validate and show the changes; change nothing.")
        parser.add_argument(
            "--skip-invalid",
            action="store_true",
            help="Change the projects that pass validation even if others don't. Otherwise nothing is changed.",
        )
        parser.add_argument("--acting-user", help="Username to record as making the changes, in their history.")

    def handle(self, csv_path, dry_run, skip_invalid, acting_user, **options):
        rows = read_legacy_csv(csv_path)
        tags_by_vol_path = starfish_tags_by_vol_path()
        vol_paths_by_project_id = {project.pk: paths for project, paths in approver_vol_paths_by_project().items()}
        plans = [plan_project(row, tags_by_vol_path, vol_paths_by_project_id) for row in rows]
        for plan in plans:
            self.stdout.write(describe_plan(plan))

        valid = [plan for plan in plans if plan.is_valid]
        invalid = len(plans) - len(valid)
        self.stdout.write(f"{len(plans)} projects: {len(valid)} valid, {invalid} invalid.")
        if dry_run:
            self.stdout.write("Dry run: nothing was changed.")
            return
        if invalid and not skip_invalid:
            raise CommandError(
                f"{invalid} projects failed validation, so nothing was changed. "
                "Fix them, or pass --skip-invalid to change the valid ones."
            )

        failed, grouper_failed = [], []
        with acting_as(acting_user):
            for plan in valid:
                where = f"Line {plan.row.line}, project {plan.row.project_id}"
                try:
                    problems = apply_plan(plan)
                except Exception as e:
                    failed.append(plan)
                    self.stderr.write(f"{where}: not changed: {e!r}")
                    continue
                if problems:
                    grouper_failed.append(plan)
                    for problem in problems:
                        self.stderr.write(f"{where}: changed in ColdFront, but {problem}")
        self.stdout.write(
            f"Changed {len(valid) - len(failed)} projects; {len(failed)} failed; "
            f"{len(grouper_failed)} had users Grouper didn't add."
        )
        if failed or grouper_failed:
            raise CommandError(
                f"{len(failed)} projects failed, and their changes were rolled back; {len(grouper_failed)} "
                "were changed in ColdFront but not all their users were added to their Grouper groups. "
                "Run the import again to retry them."
            )
