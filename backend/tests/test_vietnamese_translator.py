import io
import json
import unittest
import urllib.error
from unittest.mock import patch
from infrastructure.vietnamese_translator import GoogleVietnameseTranslator, TranslationServiceError
from pipeline.translation import translate_segments, ensure_translation_complete


class TranslatorTests(unittest.TestCase):
    def test_browser_proxy_not_used_for_translation(self):
        with patch.dict('os.environ', {'VIDTRANS_TIKTOK_PROXY': 'socks5://private:secret@host:123', 'VIDTRANS_TRANSLATION_PROXY': ''}), patch('urllib.request.build_opener') as build:
            GoogleVietnameseTranslator()
            self.assertEqual(build.call_args.args[0].proxies, {})

    def test_rejects_socks_without_exposing_credentials(self):
        with patch.dict('os.environ', {'VIDTRANS_TRANSLATION_PROXY': 'socks5://u:secret@host:123'}):
            with self.assertRaises(TranslationServiceError) as error:
                GoogleVietnameseTranslator()
            self.assertNotIn('secret', str(error.exception))

    def test_all_response_fragments_are_kept(self):
        opener = lambda *a, **k: io.BytesIO(json.dumps([[['Xin chào. '], ['Cảm ơn.']]]).encode())
        self.assertEqual(GoogleVietnameseTranslator(opener=opener).translate('你好。谢谢。'), 'Xin chào. Cảm ơn.')

    def test_provider_diagnostic_retained_and_render_still_blocked(self):
        translator = GoogleVietnameseTranslator(opener=lambda *a, **k: None)
        with patch.object(translator, 'translate', side_effect=TranslationServiceError('Google: http_429')):
            result = translate_segments([{'start': 0, 'end': 2, 'text': '你好'}], translator, retries=1)
        self.assertIn('http_429', result[0]['translation_error'])
        with self.assertRaises(RuntimeError):
            ensure_translation_complete(result)
