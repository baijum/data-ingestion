# PR CI Build Data Uploader

A command-line tool to upload CI build data for historical pull requests in a GitHub repository to Logilica.

## Prerequisites

- Python 3.6+
- GitHub personal access token with `repo` scope
- Google Cloud Storage client library (`google-cloud-storage`)
- Requests library

## Installation

1. Install required dependencies:

```bash
pip install -r requirements.txt
```

2. Make sure environment variables are set:

```bash
export GITHUB_TOKEN=your_github_token
export LOGILICA_TOKEN=your_logilica_token
```

## Usage

```bash
python pr_uploader.py --repo owner/repo [options]
```

### Options

- `--repo OWNER/REPO`: Repository name in format 'owner/repo' (required)
- `--token TOKEN`: GitHub token with repo access (can also set GITHUB_TOKEN env var)
- `--start-pr NUM`: Starting PR number to process (default: 1)
- `--end-pr NUM`: Ending PR number to process (default: latest PR)
- `--ci-context CONTEXT`: CI context to search for (default: ci/prow/e2e)

### Examples

Upload CI data for all PRs in a repository:

```bash
python pr_uploader.py --repo kubernetes/kubernetes
```

Upload CI data for PRs 1000 to 2000:

```bash
python pr_uploader.py --repo kubernetes/kubernetes --start-pr 1000 --end-pr 2000
```

Upload CI data for a specific CI context:

```bash
python pr_uploader.py --repo kubernetes/kubernetes --ci-context travis-ci/push
```

## How It Works

1. The tool fetches PR data from the GitHub API
2. For each PR, it checks for CI status with the specified context
3. When a CI status is found with a target URL pointing to GCS data, it downloads the build data
4. The tool then uploads the CI build data to Logilica using the existing upload function

## Limitations

- Currently only supports CI builds with data stored in Google Cloud Storage
- Rate limiting may apply when processing large numbers of PRs
- May need customization for different CI systems
