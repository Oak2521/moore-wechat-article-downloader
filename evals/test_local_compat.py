"""Account-free checks for the retained local Codex/Windows entrypoints."""
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import wechat_exporter
import wechat_wizard


class LocalCompatibilityTests(unittest.TestCase):
    def test_plain_key_storage_stays_explicit(self):
        parser = wechat_exporter.build_parser()
        for opt_in in (False, True):
            args = parser.parse_args(['exporter-login-qr-complete', 'synthetic-session'] +
                                     (['--allow-plain-auth-key'] if opt_in else []))
            self.assertEqual(args.allow_plain_auth_key, opt_in)
            with patch.object(wechat_exporter, 'runtime_dir', return_value=Path('synthetic-runtime')), \
                 patch.object(wechat_exporter, 'complete_qr_login', return_value={'ok': True}) as complete, \
                 patch.object(wechat_exporter, 'write_json_response'):
                wechat_exporter.command_login_qr_complete(args)
            self.assertIs(complete.call_args.args[-1], opt_in)

    def test_authorized_public_reader_template_is_offline_and_complete(self):
        with tempfile.TemporaryDirectory(prefix='wechat-local-compat-') as tmp:
            runtime, output = Path(tmp) / 'runtime', Path(tmp) / 'output'
            stdout = io.StringIO()
            with patch.dict(os.environ, {'MOORE_WECHAT_HTML_FIXTURE_DIR': str(ROOT / 'evals/fixtures/html'),
                                         'MOORE_WECHAT_EXPORTER_DISABLE_KEYCHAIN': '1',
                                         'MOORE_WECHAT_WIZARD_DISABLE_QR_AUTO': '1'}), \
                 patch('urllib.request.urlopen', side_effect=AssertionError('No network permitted in this fixture test')), \
                 redirect_stdout(stdout):
                code = wechat_wizard.main(['run', '下载：https://mp.weixin.qq.com/s/demo-url-article-1',
                                          '--runtime-dir', str(runtime), '--output-dir', str(output), '--no-assets'])
            self.assertEqual(code, 0)
            result = json.loads(stdout.getvalue())
            self.assertEqual(result['success_count'], 1)
            self.assertEqual(result['failure_count'], 0)
            actual = Path(result['output_dir'])
            self.assertTrue(actual.is_relative_to(output))
            self.assertTrue((actual / 'index.csv').is_file())
            markdown = next((actual / 'articles').glob('*.md')).read_text(encoding='utf-8')
            self.assertIn('离线 fixture 文章正文', markdown)
            self.assertIn('测试公众号', markdown)



if __name__ == '__main__':
    unittest.main()
