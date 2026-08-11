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


FROM python:3.10-slim AS runtime

# poppler-utils: pdf2image's convert_from_path (OCR page rasterization)
# tesseract-ocr: pytesseract's OCR engine
RUN apt-get update && apt-get install -y --no-install-recommends \
    poppler-utils \
    tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
COPY data ./data
COPY scripts ./scripts

RUN pip install --no-cache-dir --no-deps --no-build-isolation -e .

EXPOSE 8000

CMD ["uvicorn", "legal_graphrag.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
