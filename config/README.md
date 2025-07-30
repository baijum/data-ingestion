# Kubernetes Configuration for Prow Crawler

This directory contains Kubernetes manifests to deploy the Prow crawler as a CronJob that runs nightly.

## Files Overview

- **`cronjob.yaml`** - Main CronJob that runs the Prow crawler at 12:40 AM daily
- **`pvc.yaml`** - PersistentVolumeClaim for storing tracker files between runs
- **`configmap.yaml`** - Configuration for job URLs and processing parameters
- **`secret.yaml`** - Contains sensitive tokens (LOGILICA_TOKEN, etc.)

## Deployment Instructions

### 1. Prerequisites

Ensure you have:
- Kubernetes cluster access with appropriate permissions
- The data-ingestion Docker image built and pushed to your registry
- Required secrets configured

### 2. Deploy in Order

```bash
# 1. Create the secret (update with your actual tokens)
kubectl apply -f secret.yaml

# 2. Create the ConfigMap
kubectl apply -f configmap.yaml

# 3. Create the PersistentVolumeClaim
kubectl apply -f pvc.yaml

# 4. Create the CronJob
kubectl apply -f cronjob.yaml
```

### 3. Verify Deployment

```bash
# Check if CronJob is created
kubectl get cronjobs

# Check PVC status
kubectl get pvc prow-crawler-tracker-pvc

# View CronJob details
kubectl describe cronjob prow-crawler
```

## Configuration

### Schedule
The CronJob runs nightly at **12:40 AM** (00:40 UTC). To change the schedule, modify the `schedule` field in `cronjob.yaml`:

```yaml
spec:
  schedule: "40 0 * * *"  # minute hour day month dayofweek
```

### Job URLs
To crawl different Prow jobs, update the `configmap.yaml`:

```yaml
data:
  job-url: "https://prow.ci.openshift.org/job-history/test-platform-results/logs/YOUR-JOB-NAME"
```

Then apply the changes:
```bash
kubectl apply -f configmap.yaml
```

### Processing Limits
Adjust the number of builds processed per run in `configmap.yaml`:

```yaml
data:
  limit: "100"  # Process up to 100 builds per run
```

### Resources
Resource requests and limits are configured in `cronjob.yaml`:

```yaml
resources:
  requests:
    memory: "256Mi"
    cpu: "100m"
  limits:
    memory: "512Mi"
    cpu: "500m"
```

## Monitoring

### Check Job Status
```bash
# List recent jobs
kubectl get jobs -l app=prow-crawler

# Check job pods
kubectl get pods -l job=prow-crawler

# View logs from latest job
kubectl logs -l job=prow-crawler --tail=100
```

### Check CronJob History
```bash
# View CronJob status
kubectl describe cronjob prow-crawler

# Check successful/failed job history
kubectl get jobs -l app=prow-crawler --sort-by=.metadata.creationTimestamp
```

### View Tracker Files
The tracker files persist in the PVC. To inspect them:

```bash
# Create a temporary pod to access the PVC
kubectl run temp-pod --rm -i --tty --image=busybox --overrides='
{
  "spec": {
    "containers": [
      {
        "name": "temp-pod",
        "image": "busybox",
        "command": ["sh"],
        "volumeMounts": [
          {
            "name": "tracker-storage",
            "mountPath": "/tracker"
          }
        ]
      }
    ],
    "volumes": [
      {
        "name": "tracker-storage",
        "persistentVolumeClaim": {
          "claimName": "prow-crawler-tracker-pvc"
        }
      }
    ]
  }
}'

# Inside the pod, check tracker files
ls -la /tracker/
cat /tracker/*.txt
```

## Troubleshooting

### Job Failures
1. **Check logs**:
   ```bash
   kubectl logs -l job=prow-crawler --previous
   ```

2. **Check events**:
   ```bash
   kubectl describe cronjob prow-crawler
   kubectl get events --sort-by=.metadata.creationTimestamp
   ```

3. **Common issues**:
   - Missing LOGILICA_TOKEN in secret
   - Network connectivity issues
   - PVC mounting problems
   - Resource constraints

### CronJob Not Running
1. **Check CronJob status**:
   ```bash
   kubectl get cronjob prow-crawler -o yaml
   ```

2. **Verify schedule syntax**:
   The schedule uses standard cron format: `minute hour day month dayofweek`

3. **Check suspended status**:
   ```bash
   kubectl patch cronjob prow-crawler -p '{"spec":{"suspend":false}}'
   ```

### Manual Job Execution
To run the job manually (for testing):

```bash
kubectl create job --from=cronjob/prow-crawler manual-run-$(date +%s)
```

## Security Considerations

1. **Secrets**: Ensure secrets are properly configured and not exposed in logs
2. **RBAC**: The job only needs permissions to access ConfigMaps and Secrets
3. **Network**: The job needs outbound internet access to reach Prow and GCS
4. **Storage**: Tracker files may contain build IDs - ensure PVC is appropriately secured

## Scaling Considerations

- **Concurrency**: Set to `Forbid` to prevent overlapping jobs
- **History**: Keeps last 3 successful and 1 failed job for debugging
- **Timeout**: Job has 4-hour deadline (adjustable via `activeDeadlineSeconds`)
- **Storage**: 1Gi PVC should handle thousands of tracker entries

## Updates

To update the crawler script:
1. Build and push new Docker image
2. Update image tag in `cronjob.yaml` (or use `:latest` for automatic updates)
3. Apply the updated CronJob:
   ```bash
   kubectl apply -f cronjob.yaml
   ```