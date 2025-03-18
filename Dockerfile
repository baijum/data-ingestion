FROM --platform=linux/amd64 docker.io/library/python:3.12-slim AS builder

#FROM --platform=linux/arm64 docker.io/library/python:3.12-slim AS builder


# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    POETRY_VERSION=1.7.1 \
    APP_HOME=/app

# Create and set working directory
WORKDIR $APP_HOME

# Install dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Poetry for dependency management
RUN pip install --no-cache-dir "poetry==$POETRY_VERSION"

# Copy only dependency files to leverage Docker caching
COPY pyproject.toml poetry.lock ./
#RUN poetry config virtualenvs.create false
RUN poetry config virtualenvs.in-project true
RUN poetry config virtualenvs.create true
RUN poetry install
#--no-root

# Copy application code
COPY . .

# --- Serve Stage ---
FROM python:3.12-slim AS runtime

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
        APP_HOME=/app \
        POETRY_VIRTUALENVS_CREATE=false
    
# Create and set working directory
WORKDIR $APP_HOME
    
# Copy installed dependencies from builder stage
COPY --from=builder $APP_HOME $APP_HOME
    
# Expose the application port
EXPOSE 5002

# Command to run the application using Gunicorn
CMD ["/app/.venv/bin/gunicorn", "-w", "4", "-b", "0.0.0.0:5002", "app:app"]