#!/bin/bash

# Deploy Prow Crawler CronJob
# This script deploys the Kubernetes resources needed for the Prow crawler CronJob

set -e

echo "🚀 Deploying Prow Crawler CronJob..."

# Check if kubectl is available
if ! command -v kubectl &> /dev/null; then
    echo "❌ kubectl is not installed or not in PATH"
    exit 1
fi

# Check if we're connected to a cluster
if ! kubectl cluster-info &> /dev/null; then
    echo "❌ Not connected to a Kubernetes cluster"
    exit 1
fi

echo "✅ Connected to cluster: $(kubectl config current-context)"
echo

# Deploy in order
echo "📄 Applying ConfigMap..."
kubectl apply -f configmap.yaml

echo "💾 Applying PersistentVolumeClaim..."
kubectl apply -f pvc.yaml

echo "⏰ Applying CronJob..."
kubectl apply -f cronjob.yaml

echo
echo "🎉 Deployment complete!"
echo
echo "📊 Check deployment status:"
echo "  kubectl get cronjob prow-crawler"
echo "  kubectl get pvc prow-crawler-tracker-pvc"
echo "  kubectl describe cronjob prow-crawler"
echo
echo "📝 View logs from jobs:"
echo "  kubectl get jobs -l app=prow-crawler"
echo "  kubectl logs -l job=prow-crawler --tail=100"
echo
echo "🔧 To manually trigger a job:"
echo "  kubectl create job --from=cronjob/prow-crawler manual-run-\$(date +%s)"
echo
echo "⚙️  To update configuration:"
echo "  1. Edit configmap.yaml"
echo "  2. Run: kubectl apply -f configmap.yaml"
echo
echo "🗑️  To cleanup:"
echo "  kubectl delete cronjob prow-crawler"
echo "  kubectl delete pvc prow-crawler-tracker-pvc"
echo "  kubectl delete configmap prow-crawler-config"