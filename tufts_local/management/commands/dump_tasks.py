# myapp/management/commands/dump_tasks.py
import json
import os
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from django_q.models import Task

DELETE_BATCH = 500

"""
usage: schedule("django.core.management.call_command", "dump_tasks", "/var/archive/tasks.jsonl",
         schedule_type=Schedule.DAILY)
"""


class Command(BaseCommand):
    help = 'Archive django_q tasks older than N days to JSON Lines, then delete them'

    def add_arguments(self, parser):
        parser.add_argument('outfile')
        parser.add_argument('--days', type=int, default=90)

    def handle(self, outfile, days, **opts):
        cutoff = timezone.now() - timedelta(days=days)
        qs = Task.objects.filter(stopped__lt=cutoff).order_by('started')

        written_ids = []
        try:
            # Append so repeated runs into the same file never overwrite an archive.
            with open(outfile, 'a', encoding='utf-8') as f:
                for t in qs.iterator(chunk_size=500):
                    row = {
                        'id': t.id,
                        'name': t.name,
                        'func': t.func,
                        'hook': t.hook,
                        'group': t.group,
                        'cluster': t.cluster,
                        'started': t.started,
                        'stopped': t.stopped,
                        'success': t.success,
                        'attempt_count': t.attempt_count,
                    }
                    try:
                        row['args'] = t.args
                        row['kwargs'] = t.kwargs
                        row['result'] = t.result
                    except Exception as e:  # e.g. pickled class no longer exists
                        row['unpickle_error'] = repr(e)

                    f.write(json.dumps(row, default=str) + '\n')
                    written_ids.append(t.id)

                # Make sure the data is on disk before anything is deleted.
                f.flush()
                os.fsync(f.fileno())
        except Exception as e:
            raise CommandError(f'Dump failed, nothing deleted: {e}') from e

        # Delete only the rows that were actually written, in batches.
        deleted = 0
        for i in range(0, len(written_ids), DELETE_BATCH):
            batch = written_ids[i : i + DELETE_BATCH]
            n, _ = Task.objects.filter(id__in=batch).delete()
            deleted += n

        self.stdout.write(f'Archived {len(written_ids)} tasks to {outfile}, deleted {deleted}')
