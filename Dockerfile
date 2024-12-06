# Use Python 3.10+ as base image
FROM python:3.10-slim

# Set working directory
WORKDIR /app

# Copy requirements first to leverage Docker cache
COPY requirements.txt .

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Install additional dependencies for the API
RUN pip install --no-cache-dir fastapi uvicorn python-multipart

# Copy the entire project
COPY . .

# Create virtual environment
RUN python -m venv envsubtrans

# Expose port for API
EXPOSE 8000

# Start the API server
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"] 