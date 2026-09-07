FROM python:3.11-slim

# opencv-python-headless still needs a couple of shared libs present on
# the base image even though it's "headless" (no GUI bindings, but the
# JPEG/PNG/etc. codecs it links against expect these)
# territory: this is exactly the kind of thing that works on a dev
# machine with more packages installed and silently fails in a clean
# container image without it.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libglib2.0-0 \
    libgl1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Non-root user .
RUN useradd --create-home --uid 1000 appuser

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY alembic/ ./alembic/
COPY alembic.ini .

RUN chown -R appuser:appuser /app
USER appuser

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
