import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('image_plugin', '/opt/data/plugins/helius-imagegen/__init__.py')
plugin = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plugin)

class ImageToolTests(unittest.TestCase):
    def test_invalid_requests_never_execute(self):
        with patch.object(plugin.subprocess, 'run') as run:
            for args in ({'prompt':''}, {'prompt':'x','mode':'edit'}, {'prompt':'x','model':'other'}, {'prompt':'x','images':['/opt/data/config.yaml']}):
                self.assertFalse(json.loads(plugin.handle(args))['success'])
            run.assert_not_called()

    def test_prompt_is_data_and_warning_is_preserved(self):
        prompt = 'A cup; $(touch /tmp/should-not-exist) `id`\nwith quotes " and apostrophe \' '
        def fake_run(argv, **kwargs):
            self.assertIsInstance(argv, list)
            self.assertFalse(kwargs.get('shell', False))
            self.assertEqual(argv[:2], ['/opt/hermes/.venv/bin/python', str(plugin.SCRIPT)])
            self.assertNotIn(prompt, argv)
            self.assertEqual(Path(argv[argv.index('--prompt-file')+1]).read_text(), prompt)
            out = Path(argv[argv.index('--out')+1])
            out.with_suffix('.json').write_text(json.dumps({'images':[{'path':str(out)}], 'warnings':['Size differs']}))
            return SimpleNamespace(returncode=2, stdout='', stderr='')
        with tempfile.TemporaryDirectory() as tmp, patch.object(plugin, 'ROOT', Path(tmp)), patch.object(plugin.subprocess, 'run', side_effect=fake_run) as run:
            result = json.loads(plugin.handle({'prompt':prompt}))
            self.assertFalse(result['success'])
            self.assertEqual(result['warnings'], ['Size differs'])
            self.assertTrue(result['media'][0].startswith('MEDIA:'))
            self.assertEqual(run.call_count, 1)

    def test_timeout_does_not_retry(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(plugin, 'ROOT', Path(tmp)), patch.object(plugin.subprocess, 'run', side_effect=subprocess.TimeoutExpired('fixed-script',330)) as run:
            result = json.loads(plugin.handle({'prompt':'A cup'}))
            self.assertFalse(result['success'])
            self.assertFalse(result['retry'])
            self.assertEqual(run.call_count, 1)

if __name__ == '__main__': unittest.main()
