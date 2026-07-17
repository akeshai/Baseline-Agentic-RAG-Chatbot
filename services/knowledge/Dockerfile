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

# Copy workspace locks, config files and shared libs
COPY pyproject.toml uv.lock ./
COPY libs/shared/ ./libs/shared/
COPY services/knowledge/pyproject.toml ./services/knowledge/

# Synchronize virtual env packages (excluding project codebase)
RUN uv sync --frozen --no-install-project --package knowledge

# Install Playwright browser binaries and system packages inside the virtual environment
RUN uv run playwright install chromium --with-deps

# Copy application source files
COPY services/knowledge/app/ ./app/
COPY services/knowledge/main.py ./

# Expose FastAPI default port
EXPOSE 8000

# Start uvicorn server in hot-reload mode for development
CMD ["uv", "run", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
