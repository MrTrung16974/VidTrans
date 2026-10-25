FROM python:3.12-slim-bookworm

ARG COMFYUI_VERSION=v0.36.0
ARG IPADAPTER_COMMIT=b188a6cb39b512a9c6da7235b880af42c78ccd0d

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DEFAULT_TIMEOUT=120 \
    PIP_RETRIES=8 \
    PYTHONUNBUFFERED=1

COPY app/ /opt/comfyui/

RUN python -m pip install torch==2.7.1 torchvision==0.22.1 torchaudio==2.7.1 \
    --index-url https://download.pytorch.org/whl/cu126

ADD https://files.pythonhosted.org/packages/a8/1e/835f8b0396645a28266a87eeda6392f461a2ef87391cbf729ef9a7ae5d75/comfyui_frontend_package-1.52.7-py3-none-any.whl /tmp/comfyui_frontend_package-1.52.7-py3-none-any.whl
ADD https://files.pythonhosted.org/packages/28/0f/c30f26d33bfa2685433a7d3993d438dcff9f6d97f0b8b531f9a6793562d2/comfy_kitchen-0.2.34-cp312-abi3-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl /tmp/comfy_kitchen-0.2.34-cp312-abi3-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl

RUN python -m pip install /tmp/comfyui_frontend_package-1.52.7-py3-none-any.whl /tmp/comfy_kitchen-0.2.34-cp312-abi3-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl \
    && sed '/^comfyui-workflow-templates/d; /^comfyui-embedded-docs/d' /opt/comfyui/requirements.txt > /tmp/comfyui-requirements.txt \
    && python -m pip install -r /tmp/comfyui-requirements.txt \
    && rm -f /tmp/comfyui_frontend_package-1.52.7-py3-none-any.whl /tmp/comfy_kitchen-0.2.34-cp312-abi3-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl

# GTX 1650 is served by Comfy-kitchen's CUDA/eager backends. Triton's first
# import requires a C compiler; omitting it lets Comfy-kitchen select its
# supported CUDA fallback without shipping a build toolchain in the runtime.
RUN python -m pip uninstall -y triton

# Official ComfyUI IP-Adapter nodes, pinned so rebuilds remain reproducible.
ADD https://github.com/comfyorg/comfyui-ipadapter/archive/b188a6cb39b512a9c6da7235b880af42c78ccd0d.tar.gz /tmp/comfyui-ipadapter.tar.gz
RUN mkdir -p /opt/comfyui/custom_nodes/comfyui-ipadapter \
    && tar -xzf /tmp/comfyui-ipadapter.tar.gz --strip-components=1 -C /opt/comfyui/custom_nodes/comfyui-ipadapter \
    && rm -f /tmp/comfyui-ipadapter.tar.gz

WORKDIR /opt/comfyui

EXPOSE 8188

HEALTHCHECK --interval=15s --timeout=5s --start-period=90s --retries=20 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8188/system_stats', timeout=3).read()"

CMD ["python", "main.py", "--listen", "0.0.0.0", "--port", "8188", "--lowvram", "--disable-api-nodes", "--models-directory", "/data/models", "--input-directory", "/data/input", "--output-directory", "/data/output", "--user-directory", "/data/user"]
