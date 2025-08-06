# Prow Crawler Docker Setup

This directory contains a Docker setup for running the Prow Crawler (`prow_crawler.py`) in a containerized environment.

## Files

- `Dockerfile.prow-crawler` - Multi-stage Dockerfile optimized for running the prow crawler
- `run-prow-crawler.sh` - Convenience script for building and running the Docker container
- `.dockerignore` - Docker ignore file to exclude unnecessary files from the build context

## Quick Start

### 1. Build the Docker Image

```bash
./run-prow-crawler.sh build
```

### 2. Run the Crawler (Dry Run)

```bash
./run-prow-crawler.sh run --job-url "https://prow.ci.openshift.org/job-history/test-platform-results/logs/periodic-ci-codeready-toolchain-toolchain-e2e-master-ci-daily" --dry-run
```

### 3. Run the Crawler (Upload to Logilica)

```bash
export LOGILICA_TOKEN="your-logilica-token"
./run-prow-crawler.sh run --job-url "https://prow.ci.openshift.org/job-history/test-platform-results/logs/periodic-ci-codeready-toolchain-toolchain-e2e-master-ci-daily" --limit 10
```

## Environment Variables

| Variable | Description | Required |
|----------|-------------|----------|
| `LOGILICA_TOKEN` | Authentication token for Logilica API | Yes (unless using `--dry-run`) |
| `GOOGLE_APPLICATION_CREDENTIALS` | Path to GCS service account key file | No (anonymous access used by default) |

## Docker Image Details

### Multi-stage Build
The Dockerfile uses a multi-stage build pattern:
1. **Builder stage**: Installs Poetry and dependencies
2. **Runtime stage**: Lightweight runtime image with only necessary components

### Key Features
- Based on Python 3.12 slim image
- Uses Poetry for dependency management
- Persistent tracker directory mounted as volume
- Runs as non-root for security
- Optimized for container environments

## Usage Examples

### Show Help
```bash
./run-prow-crawler.sh run --help
```

### Process Specific Number of Builds
```bash
./run-prow-crawler.sh run --job-url "PROW_JOB_URL" --limit 25
```

### Use Custom Tracker Directory
```bash
./run-prow-crawler.sh run --job-url "PROW_JOB_URL" --tracker-dir /app/custom-tracker
```

### Dry Run (No Upload)
```bash
./run-prow-crawler.sh run --job-url "PROW_JOB_URL" --dry-run
```

## Volume Mounts

The container automatically mounts:
- `./tracker:/app/tracker` - Persistent tracker directory to avoid reprocessing builds

## Direct Docker Commands

If you prefer to use Docker directly instead of the convenience script:

### Build
```bash
docker build -f Dockerfile.prow-crawler -t prow-crawler:latest .
```

### Run
```bash
docker run --rm \
  -e LOGILICA_TOKEN="your-token" \
  -v "$(pwd)/tracker:/app/tracker" \
  prow-crawler:latest \
  --job-url "PROW_JOB_URL" \
  --dry-run
```

## Troubleshooting

### Image Not Found
If you get an "image not found" error, build the image first:
```bash
./run-prow-crawler.sh build
```

### Permission Issues
If you encounter permission issues with the tracker directory:
```bash
sudo chown -R $(id -u):$(id -g) ./tracker
```

### GCS Access Issues
For private GCS buckets, mount your service account key:
```bash
export GOOGLE_APPLICATION_CREDENTIALS="/path/to/your/key.json"
./run-prow-crawler.sh run [args]
```

### Memory Issues
If processing large numbers of builds, you might need to increase Docker's memory limit:
```bash
docker run --memory=2g --rm [other args] prow-crawler:latest [args]
```

## Development

### Rebuilding After Code Changes
```bash
./run-prow-crawler.sh build
```

### Debugging
To run the container in interactive mode for debugging:
```bash
docker run -it --rm \
  -v "$(pwd)/tracker:/app/tracker" \
  prow-crawler:latest \
  /bin/bash
```

## Security Considerations

- The container runs as a non-root user
- Sensitive environment variables are not logged
- The Docker image only includes necessary dependencies
- Uses official Python base images with security updates

## Performance Notes

- The multi-stage build reduces final image size
- Poetry virtual environment is used for clean dependency management
- Docker layer caching optimizes rebuild times
- Anonymous GCS access is used by default (no authentication required for public buckets)