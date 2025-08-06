#!/bin/bash

# Deploy Prow Crawler to OpenShift
# This script deploys the prow-crawler image, buildconfig, and cronjob

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Default values
NAMESPACE="bmuthuka-dev"
GIT_URI=""
GITHUB_SECRET=""
GENERIC_SECRET=""

# Function to print colored output
print_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
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
Usage: $0 [options]

Options:
  -n, --namespace NAMESPACE    OpenShift namespace (default: bmuthuka-dev)
  -g, --git-uri URI           Git repository URI for BuildConfig
  -h, --help                  Show this help message

Environment Variables:
  GITHUB_WEBHOOK_SECRET       Secret for GitHub webhook
  GENERIC_WEBHOOK_SECRET      Secret for generic webhook

Example:
  $0 -n my-namespace -g https://github.com/my-org/data-ingestion.git

EOF
}

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        -n|--namespace)
            NAMESPACE="$2"
            shift 2
            ;;
        -g|--git-uri)
            GIT_URI="$2"
            shift 2
            ;;
        -h|--help)
            show_usage
            exit 0
            ;;
        *)
            print_error "Unknown option: $1"
            show_usage
            exit 1
            ;;
    esac
done

# Check if logged into OpenShift
if ! oc whoami >/dev/null 2>&1; then
    print_error "Not logged into OpenShift. Please run 'oc login' first."
    exit 1
fi

# Set project/namespace
print_info "Using namespace: $NAMESPACE"
oc project "$NAMESPACE" || {
    print_error "Failed to switch to namespace $NAMESPACE"
    exit 1
}

# Deploy ImageStream
print_info "Creating ImageStream..."
oc apply -f imagestream-prow-crawler.yaml
print_success "ImageStream created/updated"

# Deploy BuildConfig
if [ -n "$GIT_URI" ]; then
    print_info "Creating BuildConfig with Git URI: $GIT_URI"
    
    # Update the BuildConfig with the provided Git URI
    sed "s|https://github.com/your-org/data-ingestion.git|$GIT_URI|g" buildconfig-prow-crawler.yaml | oc apply -f -
    
    print_success "BuildConfig created/updated"
    
    # Start a build
    print_info "Starting initial build..."
    oc start-build prow-crawler
    print_success "Build started. Check status with: oc logs -f bc/prow-crawler"
else
    print_warning "No Git URI provided. Skipping BuildConfig creation."
    print_warning "Use -g option to specify Git repository URI."
fi

# Check if required secrets exist
print_info "Checking for required secrets..."

# Check for app-secret (contains LOGILICA_TOKEN)
if ! oc get secret app-secret >/dev/null 2>&1; then
    print_warning "Secret 'app-secret' not found. Creating placeholder..."
    oc create secret generic app-secret \
        --from-literal=logilicaToken="REPLACE_WITH_ACTUAL_TOKEN"
    print_warning "Please update the secret with actual values:"
    print_warning "oc patch secret app-secret -p '{\"data\":{\"logilicaToken\":\"<base64-encoded-token>\"}}'"
fi

# Check for prow-crawler-config ConfigMap
if ! oc get configmap prow-crawler-config >/dev/null 2>&1; then
    print_info "Creating prow-crawler-config ConfigMap..."
    oc create configmap prow-crawler-config \
        --from-literal=job-url="https://prow.ci.openshift.org/job-history/test-platform-results/logs/periodic-ci-codeready-toolchain-toolchain-e2e-master-ci-daily" \
        --from-literal=limit="50" \
        --from-literal=tracker-dir="/app/tracker"
    print_success "ConfigMap created"
fi

# Check for PVC
if ! oc get pvc prow-crawler-tracker-pvc >/dev/null 2>&1; then
    print_info "Creating PersistentVolumeClaim..."
    # Check if pvc.yaml exists
    if [ -f "pvc.yaml" ]; then
        oc apply -f pvc.yaml
    else
        # Create a basic PVC
        cat << EOF | oc apply -f -
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: prow-crawler-tracker-pvc
  labels:
    app: prow-crawler
spec:
  accessModes:
    - ReadWriteOnce
  resources:
    requests:
      storage: 1Gi
EOF
    fi
    print_success "PersistentVolumeClaim created"
fi

# Deploy CronJob
print_info "Creating CronJob..."
oc apply -f cronjob.yaml
print_success "CronJob created/updated"

print_success "Prow Crawler deployment completed!"
print_info ""
print_info "Next steps:"
print_info "1. Update the app-secret with your actual LOGILICA_TOKEN"
print_info "2. Verify the prow-crawler-config ConfigMap has the correct job URL"
print_info "3. Monitor the build: oc logs -f bc/prow-crawler"
print_info "4. Check the CronJob: oc get cronjob prow-crawler"
print_info "5. Test manually: oc create job --from=cronjob/prow-crawler prow-crawler-test"