# Prow CI Job History Crawler

A command-line tool to crawl Prow job history URLs, track processed builds, and upload CI build data to Logilica. This script is designed to run periodically to capture new builds automatically.

## Features

- 🔍 **Crawls Prow Job History**: Automatically parses build IDs from Prow job history pages
- 📁 **Tracker Files**: Maintains `.txt` files to avoid duplicate processing of builds
- ⏰ **Periodic Execution**: Designed for scheduled/periodic execution via cron or CI/CD
- 🚫 **Dry Run Mode**: Test what would be processed without uploading data
- 📊 **GCS Integration**: Downloads build data from Google Cloud Storage
- 🔁 **Retry Logic**: Inherits robust retry logic from the existing upload function
- 📈 **Rate Limiting**: Built-in delays to avoid overwhelming services

## Prerequisites

- Python 3.11+ (managed via Pixi)
- Pixi package manager
- Environment variables:
  - `LOGILICA_TOKEN`: Your Logilica API token

## Installation

1. Ensure Pixi is installed and dependencies are available:
```bash
pixi install
```

2. Set required environment variables:
```bash
export LOGILICA_TOKEN=your_logilica_token
```

## Usage

### Basic Usage

```bash
pixi run python prow_crawler.py --job-url "https://prow.ci.openshift.org/job-history/test-platform-results/logs/JOB_NAME"
```

### Command Line Options

- `--job-url URL` (required): Prow job history URL to crawl
- `--tracker-dir DIR`: Directory to store tracker files (default: `./tracker`)
- `--limit NUM`: Maximum builds to process in one run (default: 50)
- `--dry-run`: Show what would be processed without uploading

### Examples

**Crawl a specific job with dry run:**
```bash
pixi run python prow_crawler.py \
  --job-url "https://prow.ci.openshift.org/job-history/test-platform-results/logs/periodic-ci-codeready-toolchain-toolchain-e2e-master-ci-daily" \
  --dry-run \
  --limit 10
```

**Process builds and upload to Logilica:**
```bash
pixi run python prow_crawler.py \
  --job-url "https://prow.ci.openshift.org/job-history/test-platform-results/logs/periodic-ci-codeready-toolchain-toolchain-e2e-master-ci-daily" \
  --limit 25
```

**Use custom tracker directory:**
```bash
pixi run python prow_crawler.py \
  --job-url "https://prow.ci.openshift.org/job-history/test-platform-results/logs/my-job" \
  --tracker-dir /path/to/trackers
```

## How It Works

### 1. URL Parsing
The script extracts the job name from the Prow job history URL:
```
https://prow.ci.openshift.org/job-history/test-platform-results/logs/JOB_NAME
                                                                        ^^^^^^^^
```

### 2. Build Discovery
- Fetches the job history HTML page
- Parses build IDs using regex pattern (15+ digit numbers)
- Removes duplicates while preserving order

### 3. Tracker Management
- Creates/reads tracker files: `./tracker/JOB_NAME.txt`
- Each line contains a processed build ID
- Prevents re-processing of already handled builds

### 4. Data Processing
For each new build:
- Downloads `finished.json` and `started.json` from GCS
- Extracts build status (success/failure)
- Constructs GCS URLs following the pattern:
  ```
  https://gcsweb-ci.apps.ci.l2s4.p1.openshiftapps.com/gcs/test-platform-results/logs/JOB_NAME/BUILD_ID/
  ```

### 5. Upload to Logilica
- Uses the existing `upload_ci_build_data()` function
- Includes retry logic (7 attempts with exponential backoff)
- Formats data according to Logilica API requirements

## Tracker Files

Tracker files are stored as plain text files:
```
./tracker/
├── periodic-ci-codeready-toolchain-toolchain-e2e-master-ci-daily.txt
├── periodic-ci-another-job-name.txt
└── ...
```

Each file contains one build ID per line:
```
1950119109857382400
1949756963222982656
1949394570059976704
...
```

## Periodic Execution

### Cron Example
Run every hour to check for new builds:
```bash
0 * * * * cd /path/to/data-ingestion && pixi run python prow_crawler.py --job-url "URL_HERE" --limit 20
```

### CI/CD Pipeline
Include in GitHub Actions, GitLab CI, or similar:
```yaml
- name: Crawl Prow builds
  run: |
    pixi run python prow_crawler.py \
      --job-url "${{ env.PROW_JOB_URL }}" \
      --limit 30
```

## Error Handling

The script includes comprehensive error handling:
- **Network errors**: Graceful failure with retry logic
- **Missing data**: Skips builds with incomplete data
- **GCS access errors**: Reports and continues with next build
- **Logilica API errors**: Uses existing retry mechanism from `app.py`

## Monitoring

Monitor the script output for:
- Number of builds found vs. processed
- Success/failure rates
- Tracker file updates
- Any error messages

Example output:
```
Crawling job: periodic-ci-codeready-toolchain-toolchain-e2e-master-ci-daily
Found 20 builds in job history
Found 15 new builds to process
Processing 15 builds (limited by --limit)

[1/15] Processing build 1950119109857382400
✓ Successfully uploaded build 1950119109857382400

Completed processing 15 builds
Successfully processed: 14
Failed to process: 1
Tracker file updated: ./tracker/periodic-ci-codeready-toolchain-toolchain-e2e-master-ci-daily.txt
```

## Troubleshooting

### Common Issues

1. **ModuleNotFoundError**: Ensure `pixi install` has been run
2. **LOGILICA_TOKEN not set**: Set the environment variable
3. **No builds found**: Check if the job URL is correct and accessible
4. **GCS access errors**: Verify the build data exists in the expected GCS location

### Debug Mode
Use `--dry-run` to test without uploading:
```bash
pixi run python prow_crawler.py --job-url "URL" --dry-run --limit 5
```

## Integration with Existing Code

The crawler leverages existing functions from `app.py`:
- `upload_ci_build_data()`: Main upload function with retry logic
- `download_single_file_from_gcs()`: GCS file download utility

This ensures consistency with the webhook-based processing already in place. 