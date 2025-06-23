from google.cloud import storage
from flask import Flask, request, abort, jsonify, make_response
import hmac
import hashlib
import json
import os
import requests
import time
import datetime

app = Flask(__name__)

GITHUB_SECRET = os.getenv("GITHUB_SECRET")
LOGILICA_TOKEN = os.getenv("LOGILICA_TOKEN")


def download_single_file_from_gcs(bucket_name: str, source_blob_name: str) -> bytes:
    """Downloads a file from Google Cloud Storage.

    Args:
        bucket_name: The name of the GCS bucket.
        source_blob_name: The GCS blob to download.
    """
    try:
        storage_client = storage.Client.create_anonymous_client()
        bucket = storage_client.bucket(bucket_name)
        blob = bucket.blob(source_blob_name)
        return blob.download_as_bytes()
    except Exception as e:
        print(f"Error downloading from GCS: {str(e)}")
        raise


def verify_signature(payload, header_signature):
    """
    Verify the HMAC signature of the payload against the signature sent by GitHub.
    """
    if header_signature is None:
        return False

    try:
        sha_name, signature = header_signature.split("=")
    except ValueError:
        return False

    if sha_name != "sha1":
        return False

    # Create the HMAC digest
    mac = hmac.new(GITHUB_SECRET.encode("utf-8"), msg=payload, digestmod=hashlib.sha1)
    return hmac.compare_digest(mac.hexdigest(), signature)


@app.route("/webhook", methods=["POST"])
def github_webhook():
    # Get the GitHub event type from the headers
    event = request.headers.get("X-GitHub-Event", "ping")

    # Handle the 'ping' event for initial webhook setup *immediately*
    if event == "ping":
        print("Received ping event, responding Pong!")
        # Using make_response to have more control over the response
        response = make_response(jsonify({"msg": "Pong!"}))
        response.status_code = 200
        response.content_type = "application/json"
        return response

    # If not a ping event, proceed with signature validation
    header_signature = request.headers.get("X-Hub-Signature")
    if header_signature is None:
        abort(400, "Signature missing")

    if not verify_signature(request.data, header_signature):
        abort(400, "Invalid signature")

    # Signature validated, parse payload for other events
    payload = request.get_json()
    print("Received event:", event)
    print("Payload:", json.dumps(payload, indent=2))

    # Process other events (like 'status')
    if event == "status":
        context = payload["context"]
        if context in ["ci/prow/e2e", "ci/prow/e2e-tests"] and payload["state"] in (
            "success",
            "failure",
        ):
            print(
                f"Processing Prow CI status event for context: {context}, state: {payload['state']}"
            )
            triggered_name = payload["commit"]["commit"]["author"]["name"]
            triggered_email = payload["commit"]["commit"]["author"]["email"]
            triggered_id = payload["commit"]["author"]["login"]
            target_url = payload["target_url"]

            # Derive original_id and name_of_payload for Prow CI (status event)
            # Assuming target_url is like https://prow.ci.openshift.org/view/gs/.../job-name/job-id
            original_id_status = target_url.split("/")[-1]
            name_of_payload_raw_status = target_url.split("/")[-2]
            name_of_payload_status = (
                "OpenShift CI " + name_of_payload_raw_status.split("-")[-1]
            )

            print(f"Downloading logs from {target_url}")
            bucket_name = "test-platform-results"
            source_prefix = target_url.split("/gs/")[-1]
            new_source_prefix = source_prefix.split("/", 1)[1]
            finished_json = json.loads(
                download_single_file_from_gcs(
                    bucket_name, new_source_prefix + "/finished.json"
                ).decode("utf-8")
            )
            started_json = json.loads(
                download_single_file_from_gcs(
                    bucket_name, new_source_prefix + "/started.json"
                ).decode("utf-8")
            )

            max_retries = 7
            retry_delay = 5  # seconds
            for attempt in range(max_retries):
                try:
                    upload_ci_build_data(
                        details_url=target_url,
                        conclusion=payload["state"],
                        started_at_epoch=started_json["timestamp"],
                        completed_at_epoch=finished_json["timestamp"],
                        repo_full_name=finished_json["metadata"]["repo"],
                        commit_sha=started_json.get("repo-commit", "unknown"),
                        triggered_name=triggered_name,
                        triggered_email=triggered_email,
                        triggered_id=triggered_id,
                        original_id=original_id_status,
                        name_of_payload=name_of_payload_status,
                    )
                    print(f"Attempt {attempt + 1}/{max_retries}: Upload successful.")
                    break  # Exit loop if upload successful
                except (
                    requests.exceptions.RequestException,
                    ValueError,
                    Exception,
                ) as e:
                    print(f"Attempt {attempt + 1}/{max_retries} failed: {str(e)}")
                    if attempt < max_retries - 1:
                        print(f"Retrying in {retry_delay} seconds...")
                        time.sleep(retry_delay)
                    else:
                        print("All retry attempts failed.")
                        # Re-raise the last exception to signal failure
                        raise

    # Process check_run events (used by Konflux CI)
    elif event == "check_run":
        check_run = payload["check_run"]
        if check_run["status"] == "completed" and check_run["conclusion"] in (
            "success",
            "failure",
        ):
            # Check if this is a Konflux CI check run
            if (
                "Red Hat Konflux" in check_run["name"]
                or "konflux" in check_run.get("details_url", "").lower()
            ):
                print(
                    f"Processing Konflux CI check_run event: {check_run['name']}, conclusion: {check_run['conclusion']}"
                )

                # Extract commit info from check_run
                head_sha = check_run["head_sha"]

                # Get commit details from repository
                repo = payload["repository"]

                # For now, just log the Konflux event - future: add specific processing
                konflux_data = {
                    "name": check_run["name"],
                    "conclusion": check_run["conclusion"],
                    "started_at": check_run["started_at"],
                    "completed_at": check_run["completed_at"],
                    "details_url": check_run["details_url"],
                    "html_url": check_run["html_url"],
                    "head_sha": head_sha,
                    "repository": repo["full_name"],
                }
                print(f"Konflux CI data: {json.dumps(konflux_data, indent=2)}")

                # Extract and prepare data for upload_ci_build_data
                konflux_conclusion = check_run["conclusion"]
                # Convert ISO 8601 strings to epoch timestamps
                started_at_dt = datetime.datetime.fromisoformat(
                    check_run["started_at"].replace("Z", "+00:00")
                )
                completed_at_dt = datetime.datetime.fromisoformat(
                    check_run["completed_at"].replace("Z", "+00:00")
                )
                konflux_started_at_epoch = int(started_at_dt.timestamp())
                konflux_completed_at_epoch = int(completed_at_dt.timestamp())
                konflux_repo_full_name = repo["full_name"]
                konflux_commit_sha = head_sha
                # For Konflux, 'triggered_name', 'triggered_email', 'triggered_id' might need to be derived
                # from commit author/committer or a specific API call if not directly available.
                # For now, using placeholder/derived values.
                konflux_triggered_name = payload["sender"]["login"]
                konflux_triggered_email = f"{payload["sender"]["login"]}@users.noreply.github.com"  # Placeholder
                konflux_triggered_id = str(payload["sender"]["id"])

                # Derive original_id and name_of_payload for Konflux CI (check_run event)
                # Using check_run id as original_id and check_run name for name_of_payload
                konflux_original_id = str(check_run["id"])
                konflux_name_of_payload = check_run["name"]

                max_retries = 7
                retry_delay = 5  # seconds
                for attempt in range(max_retries):
                    try:
                        upload_ci_build_data(
                            details_url=check_run["details_url"],
                            conclusion=konflux_conclusion,
                            started_at_epoch=konflux_started_at_epoch,
                            completed_at_epoch=konflux_completed_at_epoch,
                            repo_full_name=konflux_repo_full_name,
                            commit_sha=konflux_commit_sha,
                            triggered_name=konflux_triggered_name,
                            triggered_email=konflux_triggered_email,
                            triggered_id=konflux_triggered_id,
                            original_id=konflux_original_id,
                            name_of_payload=konflux_name_of_payload,
                        )
                        print(
                            f"Attempt {attempt + 1}/{max_retries}: Upload successful."
                        )
                        break  # Exit loop if upload successful
                    except (
                        requests.exceptions.RequestException,
                        ValueError,
                        Exception,
                    ) as e:
                        print(f"Attempt {attempt + 1}/{max_retries} failed: {str(e)}")
                        if attempt < max_retries - 1:
                            print(f"Retrying in {retry_delay} seconds...")
                            time.sleep(retry_delay)
                        else:
                            print("All retry attempts failed.")
                            # Re-raise the last exception to signal failure
                            raise

    # Log unhandled events for debugging
    if event == "status":
        print(
            f"Unhandled status event - context: {payload.get('context')}, state: {payload.get('state')}"
        )
    elif event == "check_run":
        check_run = payload["check_run"]
        print(
            f"Unhandled check_run event - name: {check_run.get('name')}, status: {check_run.get('status')}, conclusion: {check_run.get('conclusion')}"
        )
    else:
        print(f"Unhandled event type: {event}")

    # Respond with a 204 No Content status code once processing is complete
    return "", 204


# Based on https://docs.logilica.com/advanced/import/build-data
def upload_ci_build_data(
    details_url: str,
    conclusion: str,
    started_at_epoch: int,
    completed_at_epoch: int,
    repo_full_name: str,
    commit_sha: str,
    triggered_name: str,
    triggered_email: str,
    triggered_id: str,
    original_id: str,
    name_of_payload: str,
):
    breakpoint()
    try:
        logilica_token = LOGILICA_TOKEN
        if not logilica_token:
            raise ValueError("LOGILICA_TOKEN environment variable is not set")

        logilica_domain = "redhat"
        headers = {
            "Content-Type": "application/json",
            "X-lgca-token": logilica_token,
            "x-lgca-domain": logilica_domain,
        }

        # Get repository ID
        response = requests.get(
            "https://logilica.io/api/import/v1/repositories", headers=headers
        )
        response.raise_for_status()

        repo_id = ""
        data = response.json()
        for repo in data:
            if repo["name"] == repo_full_name:
                repo_id = repo["id"]
                break

        if not repo_id:
            raise ValueError(f"Repository {repo_full_name} not found in Logilica")

        url = f"https://logilica.io/api/import/v1/ci_build/{repo_id}/create"

        # Construct payload with actual data from the CI build
        payload = [
            {
                "origin": "OpenShift_CI",  # Can be updated to be dynamic if needed
                "originalID": original_id,
                "name": name_of_payload,
                "url": details_url,
                "startedAt": started_at_epoch,
                "createdAt": started_at_epoch,
                "completedAt": completed_at_epoch,
                "triggeredBy": {
                    "name": triggered_name,
                    "email": triggered_email,
                    "accountId": triggered_id,
                    "lastActivity": 1,
                },
                "status": "Completed",
                "conclusion": conclusion.capitalize(),
                "repoUrl": f"https://github.com/{repo_full_name}",
                "commit": commit_sha,
                "pullRequestUrls": [details_url],
                "isDeployment": True,
                "stages": [
                    {
                        "name": name_of_payload,
                        "id": original_id,
                        "url": details_url,
                        "startedAt": started_at_epoch,
                        "completedAt": completed_at_epoch,
                        "status": "Completed",
                        "conclusion": conclusion.capitalize(),
                        "jobs": [
                            {
                                "name": name_of_payload,
                                "startedAt": started_at_epoch,
                                "completedAt": completed_at_epoch,
                                "status": "Completed",
                                "conclusion": conclusion.capitalize(),
                            }
                        ],
                    }
                ],
            }
        ]

        response = requests.post(url, headers=headers, json=payload)
        response.raise_for_status()
        print("Successfully uploaded CI build data to Logilica")

    except requests.exceptions.RequestException as e:
        print(f"Error making request to Logilica: {str(e)}")
        raise
    except Exception as e:
        print(f"Unexpected error in upload_ci_build_data: {str(e)}")
        raise


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5001)
