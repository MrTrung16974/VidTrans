#!/bin/sh
# Write sitemap.xml once per container start; nginx fills in the public URL per request.
set -eu
html=/usr/share/nginx/html
modified="$(date -u -r "$html/index.html" +%Y-%m-%d)"
cat > "$html/sitemap.xml" <<XML
<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>__VIDTRANS_PUBLIC_URL__/</loc><lastmod>${modified}</lastmod><changefreq>weekly</changefreq><priority>1.0</priority></url>
</urlset>
XML
