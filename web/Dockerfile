# VidTrans web client: static files behind nginx. The API is a separate service
# (vidtrans), reached through VIDTRANS_API_UPSTREAM or the production gateway.
FROM nginx:stable-alpine

ENV VIDTRANS_API_UPSTREAM=http://vidtrans:8000 \
    VIDTRANS_PUBLIC_BASE_URL= \
    NGINX_ENVSUBST_FILTER=^VIDTRANS_

COPY docker/default.conf.template /etc/nginx/templates/default.conf.template
COPY docker/40-vidtrans-seo.sh /docker-entrypoint.d/40-vidtrans-seo.sh
COPY docker/robots.txt /usr/share/nginx/html/robots.txt
COPY index.html googlefffecc69420e3aa8.html /usr/share/nginx/html/
COPY assets /usr/share/nginx/html/assets
COPY js /usr/share/nginx/html/js
RUN chmod 0755 /docker-entrypoint.d/40-vidtrans-seo.sh \
    && rm -f /usr/share/nginx/html/50x.html
