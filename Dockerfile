FROM python:3.11-slim

WORKDIR /app

# Install dependencies first (layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create non-root user
RUN useradd -r -m appuser && \
    mkdir -p /app/data && \
    chown -R appuser:appuser /app/data

USER appuser

EXPOSE 8080

CMD ["python", "main.py"]
