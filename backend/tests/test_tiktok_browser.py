from __future__ import annotations

import json
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch, MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from application.tiktok_browser_routes import create_tiktok_browser_router
from infrastructure.tiktok_browser import TikTokBrowserError, TikTokBrowserManager, has_session, is_tiktok_url, login_is_limited


class TikTokBrowserTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.video = self.root / 'video.mp4'
        self.video.write_bytes(b'test video')
        self.manager = TikTokBrowserManager(self.root / 'state')

    def tearDown(self):
        self.tmp.cleanup()

    def wait_idle(self):
        self.assertTrue(self.manager._browser_lock.acquire(timeout=3))
        self.manager._browser_lock.release()

    def test_session_requires_tiktok_domain_and_unexpired_login_cookie(self):
        cookie = dict(name='sessionid', value='secret', domain='.tiktok.com', expires=time.time()+300)
        self.assertTrue(has_session([cookie]))
        self.assertFalse(has_session([{**cookie, 'domain': '.douyin.com'}]))
        self.assertFalse(has_session([{**cookie, 'expires': 1}]))
        self.assertFalse(has_session([{**cookie, 'name': 'csrf_token'}]))
        self.assertFalse(is_tiktok_url('https://www.tiktok.com.attacker.test/tiktokstudio/upload'))
        self.assertFalse(is_tiktok_url('http://www.tiktok.com/tiktokstudio/upload'))
        self.assertTrue(is_tiktok_url('https://accounts.tiktok.com/login'))

    def test_login_limit_detection_reads_challenge_iframe(self):
        normal = MagicMock()
        normal.locator.return_value.inner_text.return_value = 'Enter 6-digit code'
        challenge = MagicMock()
        challenge.locator.return_value.inner_text.return_value = (
            'Maximum number of attempts reached. Try again later.'
        )
        page = MagicMock(frames=[normal, challenge])

        self.assertTrue(self.manager._page_login_limited(page))
        self.assertTrue(login_is_limited('Maximum number of attempts reached.'))

    def test_status_detects_qr_login_without_manual_check(self):
        response = MagicMock()
        response.__enter__.return_value.status = 200
        with patch('urllib.request.urlopen', return_value=response), patch.object(self.manager, '_with_browser', return_value=True) as check:
            result = self.manager.status()
            self.assertTrue(result['session_present'])
            self.manager.status()
            self.assertEqual(check.call_count, 1)
            self.manager.status(refresh=True)
            self.assertEqual(check.call_count, 2)

    def test_check_transport_error_does_not_report_logout(self):
        response = MagicMock()
        response.__enter__.return_value.status = 200
        self.manager._session_present = True
        with patch('urllib.request.urlopen', return_value=response), patch.object(self.manager, '_with_browser', side_effect=RuntimeError('connection')):
            result = self.manager.status(refresh=True)
            self.assertTrue(result['session_present'])
            self.assertTrue(result['session_check_error'])

    def test_double_click_and_concurrent_jobs_do_not_upload_twice(self):
        started, resume = threading.Event(), threading.Event()
        calls = []
        def browser(operation):
            calls.append(1); started.set(); resume.wait(3)
        self.manager._with_browser = browser
        try:
            first = self.manager.prepare('job1', self.video, 'Caption')
            self.assertTrue(started.wait(2))
            second = self.manager.prepare('job1', self.video, 'Different caption')
            self.assertEqual(first['id'], second['id'])
            self.assertEqual(second['caption'], 'Caption')
            with self.assertRaises(TikTokBrowserError):
                self.manager.prepare('job2', self.video, 'Caption')
            with self.assertRaises(TikTokBrowserError):
                self.manager.resolve(first['id'])
            with self.assertRaises(TikTokBrowserError):
                self.manager.logout()
            self.assertEqual(len(calls), 1)
        finally:
            resume.set(); self.wait_idle()

    def test_uncertain_failure_stays_reserved_after_restart(self):
        def fail(operation):
            raise RuntimeError('sensitive browser details')
        self.manager._with_browser = fail
        first = self.manager.prepare('job1', self.video, 'Caption')
        self.wait_idle()
        self.assertEqual(self.manager.active_attempt()['state'], 'needs_review')
        restored = TikTokBrowserManager(self.root / 'state')
        self.assertEqual(restored.active_attempt()['id'], first['id'])
        self.assertNotIn('sensitive browser details', str(restored.active_attempt()))
        self.assertEqual(restored.prepare('job1', self.video, 'Caption')['id'], first['id'])

    def test_safe_upload_error_is_shown_to_user(self):
        self.manager._with_browser = lambda operation: (_ for _ in ()).throw(
            TikTokBrowserError('TikTok báo tải video thất bại')
        )
        self.manager.prepare('job1', self.video, 'Caption')
        self.wait_idle()
        self.assertEqual(self.manager.active_attempt()['message'], 'TikTok báo tải video thất bại')

    def test_restart_does_not_resume_pending_upload(self):
        with self.manager._db() as db:
            db.execute('INSERT INTO attempts (id, job_id, state, caption, message, active, created_at) VALUES (?,?,?,?,?,1,?)', ('a', 'job1', 'uploading', 'Caption', 'Uploading', time.time()))
        restored = TikTokBrowserManager(self.root / 'state')
        self.assertEqual(restored.active_attempt()['state'], 'needs_review')
        with patch.object(restored, 'status', return_value={}):
            restored.resolve('a')
        self.assertIsNone(restored.active_attempt())
        self.assertEqual(restored.latest_attempt('job1')['state'], 'resolved')

    def test_restart_marks_interrupted_publish_for_manual_review(self):
        with self.manager._db() as db:
            db.execute('INSERT INTO attempts (id, job_id, state, caption, message, active, created_at) VALUES (?,?,?,?,?,1,?)',
                       ('publish-restart', 'job1', 'publishing', 'Caption', 'Publishing', time.time()))

        restored = TikTokBrowserManager(self.root / 'state')

        self.assertEqual(restored.active_attempt()['state'], 'needs_review')
        self.assertIn('Kiểm tra bài', restored.active_attempt()['message'])

    def test_preparation_stops_for_login_before_selecting_file(self):
        manager = self.manager
        page = FakePage()
        browser = FakeBrowser(page, authenticated=False)
        with patch.object(manager, '_update') as update:
            manager._fill_upload(browser, 'a', self.video, 'Caption')
        self.assertEqual(page.uploads, [])
        self.assertEqual(update.call_args.args[1], 'needs_login')

    def test_preparation_fills_caption_without_clicking_publish(self):
        page = FakePage()
        with patch.object(self.manager, '_update') as update:
            self.manager._fill_upload(FakeBrowser(page), 'a', self.video, 'Tiếng Việt #video')
        self.assertEqual(page.uploads, [str(self.video)])
        self.assertEqual(page.goto_calls, [])
        self.assertEqual(page.caption, 'Tiếng Việt #video')
        self.assertEqual(update.call_args.args[1], 'awaiting_review')

    def test_preparation_navigates_once_when_studio_is_not_warm(self):
        page = FakePage(initial_url='https://www.tiktok.com/')
        self.manager._fill_upload(FakeBrowser(page), 'a', self.video, 'Caption')
        self.assertEqual(page.goto_calls, ['https://www.tiktok.com/tiktokstudio/upload'])
        self.assertEqual(page.uploads, [str(self.video)])

    def test_caption_is_filled_after_tiktok_finishes_upload(self):
        page = FakePage(progress=[{'percent': 40}, {'percent': 90}, {'done': True}])
        with patch.object(self.manager, '_update') as update:
            self.manager._fill_upload(FakeBrowser(page), 'a', self.video, 'Caption')
        self.assertEqual(page.upload_waits, 2)
        self.assertEqual(page.caption, 'Caption')
        self.assertTrue(any(call.kwargs.get('file_sent') for call in update.call_args_list))
        self.assertIn('tải lên xong', update.call_args.args[2])

    def test_caption_is_refilled_when_tiktok_replaces_the_editor_value(self):
        page = FakePage(caption_overwrites=[None, 'output_205deacd'])

        self.manager._fill_caption(page, 'Caption mong muốn #video')

        self.assertEqual(page.caption, 'Caption mong muốn #video')
        self.assertEqual(page.caption_fill_count, 2)

    def test_reported_upload_failure_needs_review(self):
        page = FakePage(progress=[{'failed': True}])
        with patch.object(self.manager, '_update'), self.assertRaises(TikTokBrowserError):
            self.manager._fill_upload(FakeBrowser(page), 'a', self.video, 'Caption')

    def test_only_attempts_that_sent_the_file_are_flagged(self):
        page = FakePage()
        browser = FakeBrowser(page, authenticated=False)
        self.manager._with_browser = lambda operation: operation(browser)
        self.manager.prepare('job1', self.video, 'Caption')
        self.wait_idle()
        self.assertEqual(self.manager.latest_attempt('job1')['file_sent'], 0)

    def test_ambiguous_upload_input_fails_before_upload(self):
        page = FakePage(input_count=2)
        with self.assertRaises(TikTokBrowserError):
            self.manager._fill_upload(FakeBrowser(page), 'a', self.video, 'Caption')
        self.assertEqual(page.uploads, [])

    def test_redirect_off_tiktok_refuses_file_upload(self):
        page = FakePage(initial_url='https://www.tiktok.com/', redirect='https://attacker.test/upload')
        with self.assertRaises(TikTokBrowserError):
            self.manager._fill_upload(FakeBrowser(page), 'a', self.video, 'Caption')
        self.assertEqual(page.uploads, [])

    def test_publish_clicks_post_once_after_review_state(self):
        page = FakePage()
        browser = FakeBrowser(page)
        self.manager._with_browser = lambda operation: operation(browser)
        with self.manager._db() as db:
            db.execute('INSERT INTO attempts (id, job_id, state, caption, message, active, created_at) VALUES (?,?,?,?,?,1,?)',
                       ('publish-1', 'job1', 'awaiting_review', 'Caption', 'Ready', time.time()))

        result = self.manager.publish('publish-1')

        self.assertEqual(page.post_clicks, 1)
        self.assertEqual(result['attempt']['state'], 'publish_submitted')

    def test_publish_rejects_attempt_that_is_not_ready(self):
        with self.manager._db() as db:
            db.execute('INSERT INTO attempts (id, job_id, state, caption, message, active, created_at) VALUES (?,?,?,?,?,1,?)',
                       ('publish-2', 'job1', 'needs_review', 'Caption', 'Check', time.time()))
        with self.assertRaises(TikTokBrowserError):
            self.manager.publish('publish-2')


class FakeLocator:
    def __init__(self, page, selector): self.page, self.selector = page, selector
    @property
    def first(self): return self
    def wait_for(self, **kwargs): pass
    def count(self): return self.page.input_count if 'input[' in self.selector else 1
    def set_input_files(self, path, **kwargs): self.page.uploads.append(path)
    def fill(self, caption, **kwargs):
        self.page.caption = caption
        self.page.caption_fill_count += 1
    def inner_text(self, **kwargs): return self.page.caption
    def click(self, **kwargs):
        if 'post_video_button' not in self.selector and self.selector != 'post-button':
            raise AssertionError('Preparation must not click any publish control')
        self.page.post_clicks += 1
    def is_enabled(self): return self.page.post_enabled


class FakePage:
    def __init__(self, input_count=1, redirect=None, progress=None, caption_overwrites=None,
                 initial_url='https://www.tiktok.com/tiktokstudio/upload'):
        self.url = initial_url
        self.uploads, self.caption, self.input_count, self.redirect = [], '', input_count, redirect
        self.progress, self.upload_waits, self.goto_calls = list(progress or []), 0, []
        self.caption_overwrites = list(caption_overwrites or [])
        self.caption_fill_count = 0
        self.post_clicks, self.post_enabled = 0, True
    def bring_to_front(self): pass
    def goto(self, url, **kwargs): self.goto_calls.append(url); self.url = self.redirect or url
    def locator(self, selector): return FakeLocator(self, selector)
    def get_by_role(self, role, name=None): return FakeLocator(self, 'post-button')
    def evaluate(self, script): return self.progress.pop(0) if self.progress else {'done': True}
    def wait_for_timeout(self, ms):
        if self.caption_fill_count and self.caption_overwrites:
            replacement = self.caption_overwrites.pop(0)
            if replacement is not None:
                self.caption = replacement
        elif not self.caption_fill_count:
            self.upload_waits += 1


class FakeBrowser:
    def __init__(self, page, authenticated=True):
        class Context:
            pages = [page]
            def cookies(self):
                return [dict(domain='.tiktok.com', name='sessionid', value='secret', expires=-1)] if authenticated else []
        self.contexts = [Context()]


class TikTokBrowserRouteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.output = self.root / 'outputs'; self.output.mkdir()
        (self.output / 'video.mp4').write_bytes(b'video')
        (self.output / 'post.json').write_text(json.dumps({'caption': 'Nội dung #viet', 'title': 'Title'}))
        self.manager = TikTokBrowserManager(self.root / 'state')
        self.jobs = {'good': {'status': 'completed', 'output_video': 'video.mp4', 'tiktok_json_file': 'post.json'},
                     'pending': {'status': 'processing', 'output_video': 'video.mp4'},
                     'unsafe': {'status': 'completed', 'output_video': '../secret'}}
        app = FastAPI()
        app.include_router(create_tiktok_browser_router(self.manager, self.jobs, self.output))
        self.client = TestClient(app)

    def tearDown(self): self.tmp.cleanup()

    def test_draft_uses_full_caption_and_hashtags(self):
        response = self.client.get('/api/v1/jobs/good/tiktok-draft')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['caption'], 'Nội dung #viet')

    def test_rejects_unfinished_missing_and_escaping_artifact(self):
        (self.root / 'secret').write_text('private')
        for job, expected in [('pending',409), ('missing',404), ('unsafe',404)]:
            self.assertEqual(self.client.get(f'/api/v1/jobs/{job}/tiktok-draft').status_code, expected)
        (self.output / 'video.mp4').unlink()
        self.assertEqual(self.client.get('/api/v1/jobs/good/tiktok-draft').status_code, 404)

    def test_requires_explicit_review_and_caption_limit(self):
        with patch.object(self.manager, 'prepare') as prepare:
            for data in ({'caption':'hi','reviewed':'false'}, {'caption':'hi'}, {'caption':'a'*2201,'reviewed':'true'}):
                self.assertEqual(self.client.post('/api/v1/jobs/good/tiktok-browser/prepare', data=data).status_code, 422)
            prepare.assert_not_called()

    def test_caption_suggestion_uses_translation_without_changing_artifacts(self):
        translation = self.output / 'translation.json'
        translation.write_text(json.dumps({'segments': [
            {'text': 'Kiên trì giúp bạn tiến gần hơn tới mục tiêu.'},
            {'text': 'Du lịch vòng quanh thế giới.', 'needs_review': True},
        ]}))
        self.jobs['good']['translation_file'] = translation.name
        original = (self.output / 'post.json').read_bytes()
        response = self.client.post('/api/v1/jobs/good/tiktok-caption')
        self.assertEqual(response.status_code, 200)
        self.assertIn('#kientri', response.json()['caption'])
        self.assertNotIn('#dulich', response.json()['caption'])
        self.assertEqual((self.output / 'post.json').read_bytes(), original)
        self.assertIsNone(self.manager.active_attempt())

    def test_caption_suggestion_rejects_missing_or_unreliable_translation(self):
        self.assertEqual(self.client.post('/api/v1/jobs/good/tiktok-caption').status_code, 422)
        translation = self.output / 'translation.json'
        self.jobs['good']['translation_file'] = translation.name
        translation.write_text(json.dumps({'segments': [{'text': 'Chưa chắc chắn', 'needs_review': True}]}))
        self.assertEqual(self.client.post('/api/v1/jobs/good/tiktok-caption').status_code, 422)

    def test_prepares_only_the_jobs_output_path(self):
        with patch.object(self.manager, 'prepare', return_value={'state':'queued'}) as prepare:
            response = self.client.post('/api/v1/jobs/good/tiktok-browser/prepare', data={'caption':'caption','reviewed':'true','video_path':'/secret'})
            self.assertEqual(response.status_code, 202)
            prepare.assert_called_once_with('good', (self.output / 'video.mp4').resolve(), 'caption')

    def test_publish_requires_review_and_delegates_to_manager(self):
        with patch.object(self.manager, 'publish', return_value={'attempt': {'state': 'publish_submitted'}}) as publish:
            rejected = self.client.post('/api/v1/tiktok-browser/attempts/a/publish', data={'reviewed': 'false'})
            accepted = self.client.post('/api/v1/tiktok-browser/attempts/a/publish', data={'reviewed': 'true'})
        self.assertEqual(rejected.status_code, 422)
        self.assertEqual(accepted.status_code, 200)
        publish.assert_called_once_with('a')


if __name__ == '__main__': unittest.main()
