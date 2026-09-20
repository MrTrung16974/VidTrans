import { readFileSync } from 'node:fs';
import assert from 'node:assert/strict';
const test = (name, run) => { run(); console.log(`PASS ${name}`); };
const source = readFileSync(new URL('../frontend/js/browser-frame.js', import.meta.url), 'utf8');
const { resolveTikTokFrameUrl, resolveDouyinFrameUrl, frameResponseProblem } = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
const page = 'https://169.58.116.223/#tiktok';
test('Douyin keeps a complete noVNC URL and repairs the websocket path', () => {
  for (const raw of ['http://localhost:5201/vnc_lite.html?path=websockify', '/douyin-browser/vnc_lite.html?path=websockifyvnc.html?resize=scale']) {
    const url = new URL(resolveDouyinFrameUrl(raw, page));
    assert.equal(url.pathname, '/douyin-browser/vnc_lite.html');
    assert.equal(url.searchParams.get('path'), 'douyin-browser/websockify');
  }
  assert.equal(new URL(resolveDouyinFrameUrl('http://localhost:5201/vnc_lite.html', 'http://localhost:5200')).port, '5201');
  assert.equal(resolveDouyinFrameUrl('https://www.douyin.com/', page), null);
});
test('old localhost config uses authenticated VPS proxy and websocket route', () => {
  const url = new URL(resolveTikTokFrameUrl('http://localhost:5202/vnc_lite.html?path=websockify', page));
  assert.equal(url.origin, 'https://169.58.116.223');
  assert.equal(url.pathname, '/tiktok-browser/vnc_lite.html');
  assert.equal(url.searchParams.get('path'), 'tiktok-browser/websockify');
  assert.equal(url.searchParams.get('scale'), 'true');
});
test('keeps local development browser', () => {
  assert.equal(new URL(resolveTikTokFrameUrl('http://localhost:5202/vnc_lite.html', 'http://localhost:5200')).port, '5202');
});
test('rejects real TikTok, root pages, mixed content, and credentials', () => {
  for (const raw of ['https://www.tiktok.com/tiktokstudio/upload', '/', 'javascript:alert(1)', 'http://169.58.116.223/tiktok-browser/vnc_lite.html', 'https://user:pass@169.58.116.223/tiktok-browser/vnc_lite.html']) assert.equal(resolveTikTokFrameUrl(raw, page), null);
});
test('normalizes relative VPS endpoint', () => {
  assert.equal(new URL(resolveTikTokFrameUrl('/tiktok-browser/vnc_lite.html?path=wrong', page)).searchParams.get('path'), 'tiktok-browser/websockify');
});
function response(status, values={}) { return {status, ok:status===200, headers:{get:name=>values[name]||null}}; }
test('explains auth and upstream errors', () => {
  assert.match(frameResponseProblem(response(401)), /đăng nhập/);
  assert.match(frameResponseProblem(response(502)), /HTTP 502/);
});
test('detects conflicting framing headers', () => {
  assert.match(frameResponseProblem(response(200, {'content-security-policy':"frame-ancestors 'none', frame-ancestors 'self'"})), /chặn nhúng/);
  assert.match(frameResponseProblem(response(200, {'x-frame-options':'DENY'})), /chặn nhúng/);
  assert.equal(frameResponseProblem(response(200, {'x-frame-options':'SAMEORIGIN', 'content-security-policy':"frame-ancestors 'self'"})), null);
});
