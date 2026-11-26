# syntax=docker/dockerfile:1

FROM python:3.12-slim AS build
WORKDIR /src
RUN python -m venv /venv
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN /venv/bin/pip install --no-cache-dir .

FROM python:3.12-slim
RUN useradd --system --uid 1000 --create-home kairos
COPY --from=build /venv /venv
ENV PATH="/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    KAIROS_HOST=0.0.0.0 \
    KAIROS_PORT=8080
USER kairos
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"KAIROS_PORT\", \"8080\")}/health', timeout=4)"

CMD ["kairos", "serve"]
