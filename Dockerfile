FROM python:3.12-slim-bookworm

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Create volume mount points
RUN mkdir -p /input /output

ADD . /app

WORKDIR /app
RUN uv sync --frozen

# Declare volumes
VOLUME ["/input", "/output"]

CMD ["uv", "run", "main.py"]
