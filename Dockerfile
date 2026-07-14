FROM python:3.13-slim

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install uv package manager from official image
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Set working directory
WORKDIR /workspace

# Copy dependencies lock and config files
COPY pyproject.toml uv.lock ./

# Synchronize virtual env packages (excluding project codebase)
RUN uv sync --frozen --no-install-project

# Install Playwright browser binaries and system packages inside the virtual environment
RUN uv run playwright install chromium --with-deps

# Copy application source files
COPY app/ ./app/
COPY main.py ./

# Expose FastAPI default port
EXPOSE 8000

# Start uvicorn server in hot-reload mode for development
CMD ["uv", "run", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
