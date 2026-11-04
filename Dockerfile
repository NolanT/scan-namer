FROM python:3.12-slim

# Install system dependencies for OCR and PDF rendering
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        tesseract-ocr \
        tesseract-ocr-eng \
        poppler-utils \
        ocrmypdf \
        libglib2.0-0 \
        libsm6 \
        libxext6 \
        libxrender1 \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Copy application
COPY scan_namer.py /app/scan_namer.py

WORKDIR /app

# /scans is where you mount your scan folder
VOLUME ["/scans"]

# Default: watch mode
# Override with: docker run ... scan-namer /scans (one-time batch)
ENTRYPOINT ["python", "-u", "scan_namer.py"]
CMD ["/scans", "--watch"]
