FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    curl \
    git \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source
COPY . .

# Install package (requires setup.py or pyproject.toml present)
RUN pip install -e .

# Health check pointing to the bot health server on port 8000
HEALTHCHECK --interval=30s --timeout=10s --retries=3 --start-period=40s \
    CMD curl -f http://localhost:8000/health || exit 1

# Default command: run the local bot runner (long-running)
CMD ["python", "scripts/run_bot.py", "--db", "/app/database.sqlite3"]