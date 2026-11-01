/// Runs inside the platform's own page (WebView) and returns a JSON string
/// `{"url", "id", "title"}` for the video file, `{"error"}` when the post is
/// not a video, or null while the page has not rendered its data yet.
///
/// Sources, newest layout first:
/// - TikTok: `__UNIVERSAL_DATA_FOR_REHYDRATION__` → webapp.video-detail.
/// - Douyin share page (iesdouyin.com): `window._ROUTER_DATA` → videoInfoRes.
///   `playwm` is the watermarked stream; `play` is the same video without it.
/// - Douyin web: `RENDER_DATA` (URL-encoded JSON) → aweme.detail.
/// - Any page: the `<video>` element when it plays a plain http(s) file.
const extractVideoScript = r'''
(() => {
  const http = (u) => {
    if (typeof u !== 'string' || !u) return null;
    if (u.startsWith('//')) u = 'https:' + u;
    return /^https?:\/\//.test(u) ? u : null;
  };
  const first = (list) => Array.isArray(list) ? list.map((x) => http(x && x.src ? x.src : x)).find(Boolean) || null : http(list);
  const done = (url, id, title) => JSON.stringify({ url, id: id ? String(id) : null, title: title || null });
  const script = (id) => {
    const el = document.getElementById(id);
    if (!el) return null;
    try { return JSON.parse(el.textContent); } catch (e) {}
    try { return JSON.parse(decodeURIComponent(el.textContent)); } catch (e) { return null; }
  };

  const tiktok = script('__UNIVERSAL_DATA_FOR_REHYDRATION__');
  const scope = tiktok && tiktok.__DEFAULT_SCOPE__ || {};
  const detail = scope['webapp.video-detail'] || scope['webapp.reflow.video.detail'];
  const item = detail && detail.itemInfo && detail.itemInfo.itemStruct;
  if (item) {
    const url = item.video && (first(item.video.playAddr) || first(item.video.downloadAddr));
    if (url) return done(url, item.id, item.desc);
    if (item.imagePost) return JSON.stringify({ error: 'Bài TikTok này là ảnh, không phải video' });
  }

  const router = window._ROUTER_DATA && window._ROUTER_DATA.loaderData || {};
  for (const page of Object.values(router)) {
    const aweme = page && page.videoInfoRes && (page.videoInfoRes.item_list || [])[0];
    if (!aweme) continue;
    const addr = aweme.video && aweme.video.play_addr;
    const url = addr && first(addr.url_list);
    if (url) return done(url.replace('/playwm/', '/play/'), aweme.aweme_id, aweme.desc);
    if (aweme.images && aweme.images.length) return JSON.stringify({ error: 'Bài Douyin này là ảnh, không phải video' });
  }

  const render = script('RENDER_DATA');
  if (render) {
    for (const page of Object.values(render)) {
      const aweme = page && page.aweme && page.aweme.detail;
      const url = aweme && aweme.video && (first(aweme.video.playAddr) || first(aweme.video.playApi));
      if (url) return done(url, aweme.awemeId, aweme.desc);
    }
  }

  const video = document.querySelector('video');
  const src = video && (http(video.currentSrc) || http(video.src) || first(Array.from(video.querySelectorAll('source')).map((s) => s.src)));
  return src ? done(src, null, document.title) : null;
})()
''';
