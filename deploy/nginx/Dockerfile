FROM nginx:stable-alpine

COPY 40-certificate-reload.sh /docker-entrypoint.d/40-certificate-reload.sh
RUN chmod 0755 /docker-entrypoint.d/40-certificate-reload.sh
