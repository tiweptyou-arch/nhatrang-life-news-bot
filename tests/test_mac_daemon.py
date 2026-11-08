import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import local_daemon


class MacDaemonConfigTests(unittest.TestCase):
    def test_loads_private_config_without_logging_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'secrets.json'
            path.write_text(json.dumps({
                'TELEGRAM_BOT_TOKEN': 'test-only-not-a-real-token',
                'OPENROUTER_API_KEY': 'test-only-not-a-real-key',
                'UNEXPECTED': 'ignored'
            }))
            path.chmod(0o600)
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop('TELEGRAM_BOT_TOKEN', None)
                os.environ.pop('OPENROUTER_API_KEY', None)
                local_daemon.load_private_config(path)
                self.assertEqual(os.environ['TELEGRAM_BOT_TOKEN'], 'test-only-not-a-real-token')
                self.assertEqual(os.environ['OPENROUTER_API_KEY'], 'test-only-not-a-real-key')
                self.assertNotIn('UNEXPECTED', os.environ)

    def test_rejects_private_config_with_broad_permissions(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'secrets.json'
            path.write_text('{}')
            path.chmod(0o644)
            with self.assertRaises(SystemExit) as error:
                local_daemon.load_private_config(path)
        self.assertEqual(error.exception.code, 2)


if __name__ == '__main__':
    unittest.main()
