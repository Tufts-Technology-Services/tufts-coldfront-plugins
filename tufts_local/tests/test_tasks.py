# SPDX-FileCopyrightText: (C) 2026 Tufts Technology Services (TTS)
#
# SPDX-License-Identifier: GPL-3.0-or-later

import csv
import datetime
import io

import pytest
from django.core.exceptions import ImproperlyConfigured

from tufts_local import tasks

DATA_ENGINEER = 'data.engineer@tufts.edu'


class FakeTrueNASClient:
    """Stands in for truenas_utils.TrueNASClient, which only reaches a real appliance.

    get_all_datasets() returns the four fields the real client builds out of the
    pool.dataset.details response: mountpoint, used, quota and snapshot_size.
    """

    def __init__(self, datasets):
        self.datasets = datasets

    def get_all_datasets(self):
        return self.datasets


def dataset(mountpoint='/mnt/tank/tier2/smith_lab', used=500, quota=1000, snapshot_size=25, **extra):
    return {'mountpoint': mountpoint, 'used': used, 'quota': quota, 'snapshot_size': snapshot_size, **extra}


@pytest.fixture
def truenas(monkeypatch):
    """Hand the task a fake client, and record the config id it was asked to build from."""
    calls = []

    def fake_get_truenas_client(client_config_id):
        calls.append(client_config_id)
        return FakeTrueNASClient(fake_get_truenas_client.datasets)

    fake_get_truenas_client.datasets = []
    fake_get_truenas_client.calls = calls
    monkeypatch.setattr(tasks, 'get_truenas_client', fake_get_truenas_client)
    return fake_get_truenas_client


@pytest.fixture
def mail(settings, mailoutbox):
    """A deployment configured to send, and the outbox to read the result out of.

    Nothing defines REPORTING_EMAIL_ENABLED and coldfront leaves EMAIL_SENDER empty, so
    without this the task takes its "not configured" path instead.
    """
    settings.REPORTING_EMAIL_ENABLED = True
    settings.EMAIL_SENDER = 'coldfront@tufts.edu'
    settings.EMAIL_SUBJECT_PREFIX = '[ColdFront]'
    settings.DATA_ENGINEER_EMAIL = DATA_ENGINEER
    return mailoutbox


def attached_report(mailoutbox):
    """The CSV the task attached to the message it sent."""
    _filename, content, _mimetype = mailoutbox[-1].attachments[0]
    return content


def rows_of(report):
    return list(csv.reader(io.StringIO(report)))


class TestTier2QuotaReport:
    def test_writes_a_header_and_one_row_per_dataset(self, truenas, mail):
        truenas.datasets = [
            dataset(mountpoint='/mnt/tank/tier2/smith_lab', used=500, quota=1000, snapshot_size=25),
            dataset(mountpoint='/mnt/tank/tier2/jones_lab', used=10, quota=20, snapshot_size=0),
        ]
        today = datetime.date.today().isoformat()

        tasks.update_tier2_quota_info('truenas')

        # spelled out in full: this is the file another system parses, so the column order,
        # the absence of an index column and the line endings are all part of the contract
        assert attached_report(mail) == (
            'mountpoint,used,quota,snapshot_size,report_date\n'
            f'/tier2/smith_lab,500,1000,25,{today}\n'
            f'/tier2/jones_lab,10,20,0,{today}\n'
        )

    def test_builds_the_client_from_the_given_config_id(self, truenas, mail):
        tasks.update_tier2_quota_info('tier2-appliance')

        assert truenas.calls == ['tier2-appliance']

    def test_strips_the_pool_prefix_from_mountpoints(self, truenas, mail):
        truenas.datasets = [dataset(mountpoint='/mnt/tank/tier2/smith_lab')]

        tasks.update_tier2_quota_info('truenas')

        assert rows_of(attached_report(mail))[1][0] == '/tier2/smith_lab'

    def test_keeps_mountpoints_that_do_not_carry_the_prefix(self, truenas, mail):
        truenas.datasets = [dataset(mountpoint='/mnt/other/tier2/smith_lab')]

        tasks.update_tier2_quota_info('truenas')

        assert rows_of(attached_report(mail))[1][0] == '/mnt/other/tier2/smith_lab'

    def test_leaves_out_datasets_with_no_quota(self, truenas, mail):
        """TrueNAS reports an unset refquota as null. Those datasets have had nothing
        allotted, which is not the same as having been allotted nothing."""
        truenas.datasets = [
            dataset(mountpoint='/mnt/tank/tier2/has_one', quota=1000),
            dataset(mountpoint='/mnt/tank/tier2/has_none', quota=None),
        ]

        tasks.update_tier2_quota_info('truenas')

        rows = rows_of(attached_report(mail))
        assert [row[0] for row in rows[1:]] == ['/tier2/has_one']

    def test_a_zero_quota_is_kept(self, truenas, mail):
        """0 is a quota someone set, unlike null, and dropping it would hide a dataset
        that has been deliberately pinned shut."""
        truenas.datasets = [dataset(quota=0)]

        tasks.update_tier2_quota_info('truenas')

        assert len(rows_of(attached_report(mail))) == 2

    def test_quotas_are_written_as_whole_numbers(self, truenas, mail):
        """The API hands back the parsed byte count, which arrives as a float often enough
        that 1.3e+12 would otherwise end up in the file."""
        truenas.datasets = [dataset(quota=1_000_000.0)]

        tasks.update_tier2_quota_info('truenas')

        assert rows_of(attached_report(mail))[1][2] == '1000000'

    def test_an_empty_response_still_produces_a_header(self, truenas, mail):
        truenas.datasets = []

        tasks.update_tier2_quota_info('truenas')

        assert attached_report(mail) == 'mountpoint,used,quota,snapshot_size,report_date\n'

    def test_a_field_added_upstream_lands_in_the_report(self, truenas, mail):
        """A new key in get_all_datasets() should show up rather than be dropped in
        silence -- the report is how anyone would notice it exists."""
        truenas.datasets = [dataset(compression_ratio='1.4')]

        tasks.update_tier2_quota_info('truenas')

        rows = rows_of(attached_report(mail))
        assert rows[0] == ['mountpoint', 'used', 'quota', 'snapshot_size', 'compression_ratio', 'report_date']
        assert rows[1][4] == '1.4'

    def test_a_field_missing_from_one_dataset_leaves_an_empty_cell(self, truenas, mail):
        truenas.datasets = [dataset(compression_ratio='1.4'), dataset()]

        tasks.update_tier2_quota_info('truenas')

        rows = rows_of(attached_report(mail))
        assert rows[1][4] == '1.4'
        assert rows[2][4] == ''


class TestMailingTheReport:
    def test_sends_one_message_to_the_data_engineer(self, truenas, mail):
        truenas.datasets = [dataset()]
        today = datetime.date.today().isoformat()

        tasks.update_tier2_quota_info('truenas')

        assert len(mail) == 1
        assert mail[0].to == [DATA_ENGINEER]
        assert mail[0].from_email == 'coldfront@tufts.edu'
        assert mail[0].subject == f'[ColdFront] Tier 2 quota report {today}'
        assert '1 datasets' in mail[0].body

    def test_attaches_the_report_as_a_dated_csv(self, truenas, mail):
        truenas.datasets = [dataset()]
        today = datetime.date.today().isoformat()

        tasks.update_tier2_quota_info('truenas')

        filename, _content, mimetype = mail[0].attachments[0]
        assert filename == f'tier2_quota_report_{today}.csv'
        assert mimetype == 'text/csv'

    def test_falls_back_to_the_django_sender_when_coldfront_has_none(self, truenas, mail, settings):
        """An empty EMAIL_SENDER would otherwise go out as a literal empty From header."""
        settings.EMAIL_SENDER = ''
        settings.DEFAULT_FROM_EMAIL = 'webmaster@tufts.edu'
        truenas.datasets = [dataset()]

        tasks.update_tier2_quota_info('truenas')

        assert mail[0].from_email == 'webmaster@tufts.edu'

    def test_reports_what_it_sent_as_the_task_result(self, truenas, mail):
        """django-q stores the return value and the task report page shows it, so it has
        to say what happened without carrying the whole CSV into the database."""
        truenas.datasets = [dataset(), dataset(mountpoint='/mnt/tank/tier2/jones_lab')]

        result = tasks.update_tier2_quota_info('truenas')

        assert result == {'message': f'2 datasets sent to {DATA_ENGINEER}'}

    def test_sends_nothing_when_reporting_email_is_disabled(self, truenas, mail, settings):
        """The switch is off wherever nobody has set it, and a dev instance that quietly
        mails a real person every time the schedule fires is worse than one that doesn't."""
        settings.REPORTING_EMAIL_ENABLED = False
        truenas.datasets = [dataset()]

        result = tasks.update_tier2_quota_info('truenas')

        assert mail == []
        assert result == {'message': '1 datasets; not sent, reporting email is disabled'}

    def test_the_switch_is_off_until_a_deployment_sets_it(self, truenas, settings, mailoutbox):
        """Nothing defines REPORTING_EMAIL_ENABLED, so the absent setting has to read as
        off rather than send on the strength of a name nobody has configured."""
        settings.DATA_ENGINEER_EMAIL = DATA_ENGINEER
        truenas.datasets = [dataset()]

        tasks.update_tier2_quota_info('truenas')

        assert mailoutbox == []

    def test_coldfronts_own_mail_switch_does_not_gate_the_report(self, truenas, mail, settings):
        """A deployment can keep transactional mail to users off while still sending its
        own staff the nightly reports."""
        settings.EMAIL_ENABLED = False
        truenas.datasets = [dataset()]

        tasks.update_tier2_quota_info('truenas')

        assert len(mail) == 1

    def test_an_unconfigured_recipient_is_an_error(self, truenas, settings, mailoutbox):
        """Named nobody, sent nothing: better to fail the task visibly than to let the
        report go missing every run until someone notices."""
        settings.REPORTING_EMAIL_ENABLED = True
        truenas.datasets = [dataset()]

        with pytest.raises(ImproperlyConfigured, match='DATA_ENGINEER_EMAIL'):
            tasks.update_tier2_quota_info('truenas')

        assert mailoutbox == []
