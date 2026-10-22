"""Search-engine endpoints: index.html with absolute URLs, robots.txt and sitemap.xml.

Canonical, Open Graph and JSON-LD URLs must be absolute, but the public domain is
deployment configuration (``VIDTRANS_PUBLIC_BASE_URL``), so index.html carries a
placeholder that is filled in when the page is served.
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, Response

PLACEHOLDER = "__VIDTRANS_PUBLIC_URL__"
_HOST = re.compile(r"^[A-Za-z0-9.-]+(:\d{1,5})?$")


def public_base_url(request: Request) -> str:
    configured = os.environ.get("VIDTRANS_PUBLIC_BASE_URL", "").strip().rstrip("/")
    if configured:
        return configured
    # Behind nginx the forwarded headers carry the public scheme and host.
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or ""
    host = host.split(",")[0].strip()
    if not _HOST.match(host):
        return ""
    scheme = (request.headers.get("x-forwarded-proto") or request.url.scheme or "https").split(",")[0].strip()
    return f"{'https' if scheme == 'https' else 'http'}://{host}"


def create_seo_router(frontend_dir: Path) -> APIRouter:
    router = APIRouter(include_in_schema=False)
    index_path = Path(frontend_dir) / "index.html"
    cache: dict[str, tuple[float, str]] = {}

    def index_html() -> tuple[str, float]:
        mtime = index_path.stat().st_mtime
        cached = cache.get("index")
        if cached is None or cached[0] != mtime:
            cache["index"] = (mtime, index_path.read_text(encoding="utf-8"))
        return cache["index"][1], mtime

    @router.get("/")
    @router.get("/index.html")
    def index(request: Request) -> HTMLResponse:
        html, _mtime = index_html()
        # The page shell is small and changes with every deploy; assets carry their own cache busting.
        return HTMLResponse(html.replace(PLACEHOLDER, public_base_url(request)),
                            headers={"Cache-Control": "no-cache"})

    @router.get("/robots.txt")
    def robots(request: Request) -> PlainTextResponse:
        base = public_base_url(request)
        lines = ["User-agent: *", "Allow: /", "Disallow: /api/", "Disallow: /status/", "Disallow: /download/",
                 "Disallow: /docs", "Disallow: /redoc"]
        if base:
            lines.append(f"Sitemap: {base}/sitemap.xml")
        return PlainTextResponse("\n".join(lines) + "\n", headers={"Cache-Control": "public, max-age=3600"})

    @router.get("/sitemap.xml")
    def sitemap(request: Request) -> Response:
        _html, mtime = index_html()
        modified = datetime.fromtimestamp(mtime, timezone.utc).date().isoformat()
        body = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            f"  <url><loc>{public_base_url(request)}/</loc><lastmod>{modified}</lastmod>"
            "<changefreq>weekly</changefreq><priority>1.0</priority></url>\n"
            "</urlset>\n"
        )
        return Response(body, media_type="application/xml", headers={"Cache-Control": "public, max-age=3600"})

    return router
