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
COPY instructions/ ./instructions/

# Create config directory
RUN mkdir -p /app/configs

# Set environment variables
ENV CONFIG_PATH=/app/configs/config.json
ENV DOCKER_ENV=true

# Run the service
CMD ["python", "-m", "app.main"] 