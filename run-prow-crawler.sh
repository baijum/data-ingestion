#!/bin/bash

# Script to build and run the Prow Crawler Docker container

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Default values
IMAGE_NAME="prow-crawler"
TAG="latest"
CONTAINER_NAME="prow-crawler-run"

# Function to print colored output
print_status() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Function to show usage
show_usage() {
    cat << EOF
Usage: $0 [build|run|help] [options]

Commands:
  build                     Build the Docker image
  run [args]               Run the crawler with optional arguments
  help                     Show this help message

Examples:
  $0 build                 Build the Docker image
  $0 run --help           Show prow_crawler.py help
  $0 run --job-url "https://prow.ci.openshift.org/job-history/test-platform-results/logs/periodic-ci-codeready-toolchain-toolchain-e2e-master-ci-daily" --dry-run
  $0 run --job-url "https://prow.ci.openshift.org/job-history/test-platform-results/logs/periodic-ci-codeready-toolchain-toolchain-e2e-master-ci-daily" --limit 10

Environment Variables:
  LOGILICA_TOKEN           Required for uploading data to Logilica
  GOOGLE_APPLICATION_CREDENTIALS  Path to GCS service account key (if needed)

EOF
}

# Function to build the Docker image
build_image() {
    print_status "Building Docker image: $IMAGE_NAME:$TAG"
    docker build -f Dockerfile.prow-crawler -t "$IMAGE_NAME:$TAG" .
    print_status "Docker image built successfully!"
}

# Function to run the container
run_container() {
    local args="$@"
    
    # Check if LOGILICA_TOKEN is set
    if [ -z "$LOGILICA_TOKEN" ]; then
        print_warning "LOGILICA_TOKEN environment variable not set. Use --dry-run for testing."
    fi
    
    # Prepare environment variables
    local env_vars=""
    if [ ! -z "$LOGILICA_TOKEN" ]; then
        env_vars="$env_vars -e LOGILICA_TOKEN=$LOGILICA_TOKEN"
    fi
    
    if [ ! -z "$GOOGLE_APPLICATION_CREDENTIALS" ]; then
        env_vars="$env_vars -e GOOGLE_APPLICATION_CREDENTIALS=/app/gcs-key.json"
        env_vars="$env_vars -v $GOOGLE_APPLICATION_CREDENTIALS:/app/gcs-key.json:ro"
    fi
    
    # Create local tracker directory if it doesn't exist
    mkdir -p ./tracker
    
    print_status "Running prow_crawler.py with args: $args"
    
    # Remove any existing container with the same name
    docker rm -f "$CONTAINER_NAME" 2>/dev/null || true
    
    # Run the container
    docker run --rm \
        --name "$CONTAINER_NAME" \
        $env_vars \
        -v "$(pwd)/tracker:/app/tracker" \
        "$IMAGE_NAME:$TAG" \
        $args
}

# Main script logic
case "${1:-help}" in
    "build")
        build_image
        ;;
    "run")
        shift # Remove 'run' from arguments
        # Check if image exists
        if ! docker image inspect "$IMAGE_NAME:$TAG" >/dev/null 2>&1; then
            print_warning "Docker image $IMAGE_NAME:$TAG not found. Building it first..."
            build_image
        fi
        run_container "$@"
        ;;
    "help"|"--help"|"-h")
        show_usage
        ;;
    *)
        print_error "Unknown command: $1"
        show_usage
        exit 1
        ;;
esac