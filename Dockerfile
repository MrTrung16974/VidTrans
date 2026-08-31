FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Debian mirrors can occasionally be unavailable while Docker is resolving DNS.
# Retry the complete update/install transaction so a short-lived lookup failure
# does not make an otherwise valid image build fail.
RUN set -eux; \
    for attempt in 1 2 3 4 5; do \
        rm -rf /var/lib/apt/lists/*; \
        if apt-get update -o Acquire::Retries=3 \
            && apt-get install -y --no-install-recommends -o Acquire::Retries=3 \
                ffmpeg \
                libass9 \
                fontconfig \
                fonts-dejavu-core; then \
            break; \
        fi; \
        if [ "$attempt" -eq 5 ]; then exit 1; fi; \
        sleep "$((attempt * 5))"; \
    done; \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
# Install the CPU wheel first. Otherwise the unconstrained Whisper dependency may
# pull several gigabytes of CUDA libraries into a Docker Desktop CPU image.
RUN pip install --upgrade pip \
    && pip install --index-url https://download.pytorch.org/whl/cpu torch==2.5.1 \
    && pip install -r requirements.txt

COPY requirements-downloader.txt ./
RUN pip install -r requirements-downloader.txt

# Chromium is isolated in its own late layer so adding QR login does not
# invalidate the expensive Torch/Paddle dependency installation above.
RUN apt-get update -o Acquire::Retries=3 \
    && apt-get install -y --no-install-recommends -o Acquire::Retries=3 chromium \
    && rm -rf /var/lib/apt/lists/*

COPY backend ./backend
RUN mkdir -p /usr/local/share/fonts/vidtrans \
    && cp /app/backend/assets/fonts/NotoSansCJKsc-Regular.otf /usr/local/share/fonts/vidtrans/ \
    && fc-cache -f \
    && mkdir -p /app/backend/uploads /app/backend/outputs /app/backend/work

EXPOSE 8000
WORKDIR /app/backend
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
