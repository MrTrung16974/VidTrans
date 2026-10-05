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


class RateLimitTests(unittest.TestCase):
    def _http_429(self, *a, **k):
        raise urllib.error.HTTPError('https://x', 429, 'Too Many Requests', {'Retry-After': '7'}, None)

    def test_both_endpoints_429_raises_rate_limited_with_retry_after(self):
        from infrastructure.vietnamese_translator import TranslationRateLimitedError
        with self.assertRaises(TranslationRateLimitedError) as error:
            GoogleVietnameseTranslator(opener=self._http_429).translate('你好')
        self.assertEqual(error.exception.retry_after, 7.0)

    def test_translate_lines_uses_one_request(self):
        calls = []
        def opener(request, timeout):
            calls.append(request)
            return io.BytesIO(json.dumps([[['Xin chào\n'], ['Cảm ơn']]]).encode())
        result = GoogleVietnameseTranslator(opener=opener).translate_lines(['你好', '谢谢'])
        self.assertEqual(result, ['Xin chào', 'Cảm ơn'])
        self.assertEqual(len(calls), 1)

    def test_pipeline_batches_lines_for_google(self):
        calls = []
        def opener(request, timeout):
            calls.append(request)
            return io.BytesIO(json.dumps([[['Một\nHai\nBa']]]).encode())
        segments = [{'start': i, 'end': i + 1, 'text': t} for i, t in enumerate(['一个', '两个', '三个'])]
        result = translate_segments(segments, GoogleVietnameseTranslator(opener=opener), sleeper=lambda s: None)
        self.assertEqual([s['text'] for s in result], ['Một', 'Hai', 'Ba'])
        self.assertEqual(len(calls), 1)
        ensure_translation_complete(result)

    def test_rate_limit_backs_off_then_stops_hammering(self):
        sleeps, calls = [], []
        def opener(*a, **k):
            calls.append(1)
            self._http_429()
        segments = [{'start': i, 'end': i + 1, 'text': t} for i, t in enumerate(['一个', '两个', '三个'])]
        result = translate_segments(segments, GoogleVietnameseTranslator(opener=opener), sleeper=sleeps.append)
        self.assertTrue(all(s['translation_status'] == 'source_fallback' for s in result))
        self.assertTrue(sleeps and max(sleeps) >= 7.0)
        # Batch (5 attempts) + first cue (5 attempts x 2 endpoints); remaining cues are not retried.
        self.assertEqual(len(calls), 5 + 10)
        with self.assertRaises(RuntimeError) as error:
            ensure_translation_complete(result)
        self.assertIn('VIDTRANS_TRANSLATION_PROXY', str(error.exception))
