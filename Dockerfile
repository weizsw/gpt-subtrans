# Use Python 3.10+ as base image
FROM python:3.10-slim

# Set working directory
WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app/ ./app/
COPY PySubtitle/ ./PySubtitle/
COPY scripts/ ./scripts/

# Create config directory
RUN mkdir -p /app/config

# Set environment variables
ENV REDIS_URL=redis://redis:6379
ENV CONFIG_PATH=/app/config/config.json

# Run the service
CMD ["python", "-m", "app.main"] 