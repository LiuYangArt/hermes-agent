"""Release boundaries between the gateway and independently owned cron jobs."""
import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from team import manage


class JobDeploymentTests(unittest.TestCase):
    def test_core_install_preserves_live_job_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            (state / 'data').mkdir()
            (state / 'data/config.yaml').write_text('{}')
            (state / 'data/team-settings.md').write_text('test')
            scripts = state / 'data/scripts'
            (scripts / 'meegle_triage').mkdir(parents=True)
            targets = [scripts / 'meegle_triage/triage.py', scripts / 'cron_daily_summary.py']
            for target in targets:
                target.write_text('live task customization')
            with patch.object(manage, 'STATE', state), contextlib.redirect_stdout(io.StringIO()):
                manage.install()
            for target in targets:
                self.assertEqual(target.read_text(), 'live task customization')

    def test_named_release_preserves_other_task_and_job_state(self):
        for name in manage.JOBS:
            with self.subTest(job=name), tempfile.TemporaryDirectory() as directory:
                state = Path(directory)
                (state / 'data').mkdir()
                (state / 'data/config.yaml').write_text('{}')
                with patch.object(manage, 'STATE', state), contextlib.redirect_stdout(io.StringIO()):
                    other = next(job for job in manage.JOBS if job != name)
                    other_files = [target for _, target in manage.job_assets(other)]
                    protected = other_files + [state / 'data/cron/jobs.json', state / 'data/triage/config.json']
                    for target in protected:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_text('preserved')
                    manage.install_job(name)
                    manage.verify_job(name)
                    for target in protected:
                        self.assertEqual(target.read_text(), 'preserved')


if __name__ == '__main__':
    unittest.main()
