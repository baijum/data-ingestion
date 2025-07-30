#!/usr/bin/env python3
"""
Prow CI Job History Crawler

This script crawls Prow job history URLs to find new builds, downloads their data,
and uploads them to Logilica. It maintains a tracker file to avoid duplicate processing.
"""

import argparse
import json
import os
import requests
import sys
import time
from datetime import datetime
from typing import List, Dict, Any, Optional, Set
from urllib.parse import urljoin, urlparse
import re

# Import the upload function from app.py
from app import upload_ci_build_data, download_single_file_from_gcs


def configure_parser() -> argparse.ArgumentParser:
    """Configure command line argument parser."""
    parser = argparse.ArgumentParser(
        description="Crawl Prow job history URLs and upload CI build data to Logilica"
    )
    parser.add_argument(
        "--job-url",
        required=True,
        help="Prow job history URL (e.g., https://prow.ci.openshift.org/job-history/test-platform-results/logs/periodic-ci-codeready-toolchain-toolchain-e2e-master-ci-daily)",
    )
    parser.add_argument(
        "--tracker-dir",
        default="./tracker",
        help="Directory to store tracker files (default: ./tracker)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=50,
        help="Maximum number of builds to process in one run (default: 50)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only show what would be processed without uploading",
    )
    return parser


def get_job_name_from_url(job_url: str) -> str:
    """Extract job name from Prow job history URL."""
    # URL format: https://prow.ci.openshift.org/job-history/test-platform-results/logs/JOB_NAME
    parts = job_url.rstrip("/").split("/")
    if len(parts) < 2:
        raise ValueError(f"Invalid job URL format: {job_url}")
    return parts[-1]


def get_tracker_file_path(tracker_dir: str, job_name: str) -> str:
    """Get the path to the tracker file for a job."""
    os.makedirs(tracker_dir, exist_ok=True)
    return os.path.join(tracker_dir, f"{job_name}.txt")


def load_processed_builds(tracker_file: str) -> Set[str]:
    """Load the set of already processed build IDs from tracker file."""
    if not os.path.exists(tracker_file):
        return set()
    
    try:
        with open(tracker_file, "r") as f:
            return set(line.strip() for line in f if line.strip())
    except Exception as e:
        print(f"Warning: Could not read tracker file {tracker_file}: {e}")
        return set()


def save_processed_build(tracker_file: str, build_id: str):
    """Append a build ID to the tracker file."""
    try:
        with open(tracker_file, "a") as f:
            f.write(f"{build_id}\n")
    except Exception as e:
        print(f"Error: Could not write to tracker file {tracker_file}: {e}")


def fetch_job_history(job_url: str) -> Optional[str]:
    """Fetch the job history page and return the HTML content."""
    try:
        response = requests.get(job_url, timeout=30)
        response.raise_for_status()
        return response.text
    except requests.RequestException as e:
        print(f"Error fetching job history from {job_url}: {e}")
        return None


def parse_build_ids_from_html(html_content: str) -> List[str]:
    """Parse build IDs from the job history HTML page."""
    # Look for build IDs in table rows - they're typically long numeric strings
    # Pattern matches long numeric IDs (at least 15 digits)
    build_pattern = r'\b(\d{15,})\b'
    matches = re.findall(build_pattern, html_content)
    
    # Remove duplicates while preserving order
    seen = set()
    builds = []
    for match in matches:
        if match not in seen:
            seen.add(match)
            builds.append(match)
    
    return builds


def construct_gcs_url(job_name: str, build_id: str) -> str:
    """Construct GCS URL for a build."""
    # Format: https://gcsweb-ci.apps.ci.l2s4.p1.openshiftapps.com/gcs/test-platform-results/logs/JOB_NAME/BUILD_ID/
    return f"https://gcsweb-ci.apps.ci.l2s4.p1.openshiftapps.com/gcs/test-platform-results/logs/{job_name}/{build_id}/"


def fetch_build_data(job_name: str, build_id: str) -> Optional[Dict[str, Any]]:
    """Fetch build data from GCS."""
    bucket_name = "test-platform-results"
    base_path = f"logs/{job_name}/{build_id}"
    
    try:
        # Download finished.json and started.json
        finished_data = download_single_file_from_gcs(
            bucket_name, f"{base_path}/finished.json"
        )
        started_data = download_single_file_from_gcs(
            bucket_name, f"{base_path}/started.json"
        )
        
        finished_json = json.loads(finished_data.decode("utf-8"))
        started_json = json.loads(started_data.decode("utf-8"))
        
        return {
            "finished": finished_json,
            "started": started_json,
            "gcs_url": construct_gcs_url(job_name, build_id),
            "build_id": build_id,
            "job_name": job_name,
        }
    except Exception as e:
        print(f"Error fetching build data for {job_name}/{build_id}: {e}")
        return None


def determine_build_status(finished_json: Dict[str, Any]) -> str:
    """Determine build status from finished.json."""
    result = finished_json.get("result", "").upper()
    if result == "SUCCESS":
        return "success"
    elif result in ["FAILURE", "FAILED"]:
        return "failure"
    else:
        # Default to failure for unknown states
        return "failure"


def extract_repo_info(finished_json: Dict[str, Any], started_json: Dict[str, Any]) -> Dict[str, str]:
    """Extract repository information from build data."""
    # Try to get repo from finished.json metadata
    repo_full_name = finished_json.get("metadata", {}).get("repo", "")
    if not repo_full_name:
        # Fallback: try to derive from job data or use a default
        repo_full_name = "openshift/unknown"  # Default fallback
    
    # Get commit SHA
    commit_sha = started_json.get("repo-commit", "unknown")
    
    return {
        "repo_full_name": repo_full_name,
        "commit_sha": commit_sha,
    }


def get_triggered_info(job_name: str) -> Dict[str, str]:
    """Get triggered user info - for periodic jobs, use system info."""
    # For periodic jobs, use system account
    return {
        "triggered_name": "OpenShift CI System",
        "triggered_email": "openshift-ci@redhat.com",
        "triggered_id": "openshift-ci-robot",
    }


def process_build(
    job_name: str, 
    build_id: str, 
    dry_run: bool = False
) -> bool:
    """Process a single build and upload its data."""
    print(f"Processing build {build_id} for job {job_name}...")
    
    # Fetch build data
    build_data = fetch_build_data(job_name, build_id)
    if not build_data:
        print(f"Skipping build {build_id}: Could not fetch build data")
        return False
    
    finished_json = build_data["finished"]
    started_json = build_data["started"]
    gcs_url = build_data["gcs_url"]
    
    # Determine build status
    conclusion = determine_build_status(finished_json)
    
    # Extract repo info
    repo_info = extract_repo_info(finished_json, started_json)
    
    # Get timestamps
    started_at_epoch = started_json.get("timestamp", 0)
    completed_at_epoch = finished_json.get("timestamp", 0)
    
    # Get triggered user info
    triggered_info = get_triggered_info(job_name)
    
    # Derive name_of_payload similar to app.py logic
    name_of_payload = f"OpenShift CI {job_name.split('-')[-1]}"
    
    if dry_run:
        print(f"[DRY RUN] Would upload build {build_id}:")
        print(f"  - Job: {job_name}")
        print(f"  - Status: {conclusion}")
        print(f"  - Repo: {repo_info['repo_full_name']}")
        print(f"  - Commit: {repo_info['commit_sha']}")
        print(f"  - Started: {datetime.fromtimestamp(started_at_epoch)}")
        print(f"  - Completed: {datetime.fromtimestamp(completed_at_epoch)}")
        print(f"  - GCS URL: {gcs_url}")
        return True
    
    # Upload to Logilica
    try:
        upload_ci_build_data(
            details_url=gcs_url,
            conclusion=conclusion,
            started_at_epoch=started_at_epoch,
            completed_at_epoch=completed_at_epoch,
            repo_full_name=repo_info["repo_full_name"],
            commit_sha=repo_info["commit_sha"],
            triggered_name=triggered_info["triggered_name"],
            triggered_email=triggered_info["triggered_email"],
            triggered_id=triggered_info["triggered_id"],
            original_id=build_id,
            name_of_payload=name_of_payload,
        )
        print(f"✓ Successfully uploaded build {build_id}")
        return True
    except Exception as e:
        print(f"✗ Failed to upload build {build_id}: {str(e)}")
        return False


def main():
    parser = configure_parser()
    args = parser.parse_args()
    
    job_url = args.job_url.rstrip("/")
    tracker_dir = args.tracker_dir
    limit = args.limit
    dry_run = args.dry_run
    
    # Extract job name from URL
    try:
        job_name = get_job_name_from_url(job_url)
    except ValueError as e:
        sys.exit(f"Error: {e}")
    
    print(f"Crawling job: {job_name}")
    print(f"Job URL: {job_url}")
    print(f"Tracker directory: {tracker_dir}")
    print(f"Limit: {limit} builds")
    if dry_run:
        print("DRY RUN MODE - No data will be uploaded")
    print()
    
    # Get tracker file path
    tracker_file = get_tracker_file_path(tracker_dir, job_name)
    
    # Load already processed builds
    processed_builds = load_processed_builds(tracker_file)
    print(f"Already processed {len(processed_builds)} builds")
    
    # Fetch job history page
    html_content = fetch_job_history(job_url)
    if not html_content:
        sys.exit("Error: Could not fetch job history page")
    
    # Parse build IDs from HTML
    all_builds = parse_build_ids_from_html(html_content)
    print(f"Found {len(all_builds)} builds in job history")
    
    # Filter out already processed builds
    new_builds = [build for build in all_builds if build not in processed_builds]
    print(f"Found {len(new_builds)} new builds to process")
    
    if not new_builds:
        print("No new builds to process")
        return
    
    # Limit the number of builds to process
    builds_to_process = new_builds[:limit]
    if len(builds_to_process) < len(new_builds):
        print(f"Processing {len(builds_to_process)} builds (limited by --limit)")
    
    # Process builds
    success_count = 0
    for i, build_id in enumerate(builds_to_process, 1):
        print(f"\n[{i}/{len(builds_to_process)}] Processing build {build_id}")
        
        if process_build(job_name, build_id, dry_run):
            success_count += 1
            if not dry_run:
                save_processed_build(tracker_file, build_id)
        
        # Add delay to avoid overwhelming the services
        if i < len(builds_to_process):
            time.sleep(1)
    
    print(f"\nCompleted processing {len(builds_to_process)} builds")
    print(f"Successfully processed: {success_count}")
    print(f"Failed to process: {len(builds_to_process) - success_count}")
    
    if not dry_run and success_count > 0:
        print(f"Tracker file updated: {tracker_file}")


if __name__ == "__main__":
    main() 