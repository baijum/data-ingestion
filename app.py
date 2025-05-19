from google.cloud import storage
from flask import Flask, request, abort, jsonify, make_response
import hmac
import hashlib
import json
import os
import requests
import time

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
                f"Processing status event for context: {context}, state: {payload['state']}"
            )
            triggered_name = payload["commit"]["commit"]["author"]["name"]
            triggered_email = payload["commit"]["commit"]["author"]["email"]
            triggered_id = payload["commit"]["author"]["login"]
            target_url = payload["target_url"]
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
                        target_url,
                        finished_json,
                        started_json,
                        triggered_name,
                        triggered_email,
                        triggered_id,
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

    # Log unhandled events for debugging
    if event == "status":
        print(
            f"Unhandled status event - context: {payload.get('context')}, state: {payload.get('state')}"
        )
    else:
        print(f"Unhandled event type: {event}")

    # Respond with a 204 No Content status code once processing is complete
    return "", 204


# Based on https://docs.logilica.com/advanced/import/build-data
def upload_ci_build_data(
    details_url: str,
    finished_json: dict,
    started_json: dict,
    triggered_name: str,
    triggered_email: str,
    triggered_id: str,
):
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
            if repo["name"] == finished_json["metadata"]["repo"]:
                repo_id = repo["id"]
                break

        if not repo_id:
            raise ValueError(
                f"Repository {finished_json['metadata']['repo']} not found in Logilica"
            )

        url = f"https://logilica.io/api/import/v1/ci_build/{repo_id}/create"
        details = details_url.split("/pull/")[1]
        original_id = details.split("/")[3]
        name_of_payload = details.split("/")[2]
        name_of_payload = "OpenShift CI " + name_of_payload.split("-")[-1]

        # Construct payload with actual data from the CI build
        payload = [
            {
                "origin": "OpenShift_CI",
                "originalID": original_id,
                "name": name_of_payload,
                "url": details_url,
                "startedAt": started_json["timestamp"],
                "createdAt": started_json["timestamp"],
                "completedAt": finished_json["timestamp"],
                "triggeredBy": {
                    "name": triggered_name,
                    "email": triggered_email,
                    "accountId": triggered_id,
                    "lastActivity": 1,
                },
                "status": "Completed",
                "conclusion": finished_json["result"].capitalize(),
                "repoUrl": "https://github.com/"
                + finished_json.get("metadata", {}).get("repo", "unknown"),
                "commit": started_json.get("repo-commit", "unknown"),
                "pullRequestUrls": [details_url],
                "isDeployment": True,
                "stages": [
                    {
                        "name": name_of_payload,
                        "id": original_id,
                        "url": details_url,
                        "startedAt": started_json["timestamp"],
                        "completedAt": finished_json["timestamp"],
                        "status": "Completed",
                        "conclusion": finished_json["result"].capitalize(),
                        "jobs": [
                            {
                                "name": name_of_payload,
                                "startedAt": started_json["timestamp"],
                                "completedAt": finished_json["timestamp"],
                                "status": "Completed",
                                "conclusion": finished_json["result"].capitalize(),
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
