# Multi-stage build for the FastAPI app: a builder stage installs Python deps
# (including native-wheel-dependent packages like torch/onnxruntime) into a
# venv, then the runtime stage copies just that venv plus the app source, so
# the final image doesn't carry build toolchains it doesn't need at runtime.

FROM python:3.10-slim AS builder

WORKDIR /build

# poppler-utils (pdf2image) and tesseract-ocr (pytesseract) are needed at
# runtime too, but building the venv doesn't need them; installed once here so
# the runtime stage below can just apt-get install them without a compiler.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch==2.13.0 \
    && pip install --no-cache-dir -r requirements.txt --extra-index-url https://download.pytorch.org/whl/cpu

# Strip artifacts the running app never reads. Done in the builder stage so the
# runtime COPY brings across only what is left:
#   torch/test, torch/include - C++ headers and test binaries, needed to compile
#     torch extensions, not to run inference
#   */tests, */test - test suites vendored inside installed packages
#   __pycache__ - regenerated on demand at import time
RUN cd /opt/venv/lib/python3.10/site-packages \
    && rm -rf torch/test torch/include \
    && find . -maxdepth 2 -type d -name test -prune -exec rm -rf {} + \
    && find . -maxdepth 2 -type d -name tests -prune -exec rm -rf {} + \
    && find . -type d -name __pycache__ -prune -exec rm -rf {} + \
    && find . -type f -name "*.pyi" -delete


FROM python:3.10-slim AS runtime

# poppler-utils: pdf2image's convert_from_path (OCR page rasterization)
# tesseract-ocr: pytesseract's OCR engine
# gosu: lets the entrypoint start as root (to chown volume mounts) then drop
# to appuser before exec'ing uvicorn, without su's TTY/signal-forwarding quirks
RUN apt-get update && apt-get install -y --no-install-recommends \
    poppler-utils \
    tesseract-ocr \
    gosu \
    && rm -rf /var/lib/apt/lists/*

# appuser is created before the venv arrives so the COPY can set ownership
# directly with --chown. Creating it afterwards and running
# `chown -R appuser:appuser /opt/venv` rewrote every file in the venv, which
# Docker stores as a second full copy: the 2GB venv was held twice and the
# image carried over 4GB for 2GB of packages.
RUN useradd --create-home --shell /bin/bash appuser

COPY --from=builder --chown=appuser:appuser /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
COPY data ./data
COPY scripts ./scripts
# tests/eval is imported at runtime by POST /api/eval/refresh (main.py adds it
# to sys.path and does `from run_eval import main`) -- without it the
# Operations Dashboard's "Run Fresh Eval" button 500s with ModuleNotFoundError
# inside the container even though the same endpoint works fine locally where
# tests/ exists on disk. Only tests/eval, not the whole tests/ tree, to avoid
# pulling in unrelated pytest suites that need dev-only fixtures.
COPY tests/eval ./tests/eval

RUN pip install --no-cache-dir --no-deps --no-build-isolation -e .

# appuser already exists (created above, before the venv copy). Only /app needs
# chowning here, and it is a few MB rather than the 2GB venv.
RUN mkdir -p /app/data/chroma_db /app/data/uploads \
    && chown -R appuser:appuser /app

COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=5s --retries=10 --start-period=120s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

# Stays root here so the entrypoint can chown volume mounts, then drops to
# appuser itself before exec'ing the CMD below (see docker-entrypoint.sh).
ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["uvicorn", "counsel_graph.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
