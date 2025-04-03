from google.cloud import storage
from flask import Flask, request, abort
import hmac
import hashlib
import json
import os
import requests

app = Flask(__name__)

GITHUB_SECRET = os.getenv('GITHUB_SECRET')
LOGILICA_TOKEN = os.getenv('LOGILICA_TOKEN')


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
        sha_name, signature = header_signature.split('=')
    except ValueError:
        return False

    if sha_name != 'sha1':
        return False

    # Create the HMAC digest
    mac = hmac.new(GITHUB_SECRET.encode('utf-8'), msg=payload, digestmod=hashlib.sha1)
    return hmac.compare_digest(mac.hexdigest(), signature)

@app.route('/webhook', methods=['POST'])
def github_webhook():
    # Retrieve signature from headers
    header_signature = request.headers.get('X-Hub-Signature')
    if header_signature is None:
        abort(400, 'Signature missing')

    # Validate the request payload signature
    if not verify_signature(request.data, header_signature):
        abort(400, 'Invalid signature')

    # Get the GitHub event type from the headers
    event = request.headers.get('X-GitHub-Event', 'ping')

    # Parse the JSON payload
    payload = request.get_json()
    print("Received event:", event)
    print("Payload:", json.dumps(payload, indent=2))

    # Handle the 'ping' event for initial webhook setup
    if event == 'ping':
        return json.dumps({'msg': 'Pong!'}), 200

    if event == 'status':
        context = payload['context']
        if context == "ci/prow/e2e" and payload['state'] in ("success", "failure"):
            triggered_name = payload['commit']['commit']['author']['name']
            triggered_email = payload['commit']['commit']['author']['email']
            triggered_id =  payload['commit']['author']['login']
            target_url = payload['target_url']
            print(f"Downloading logs from {target_url}")
            bucket_name = "test-platform-results"
            source_prefix = target_url.split("/gs/")[-1]
            new_source_prefix = source_prefix.split("/",1)[1]
            finished_json = json.loads(download_single_file_from_gcs(bucket_name, new_source_prefix+"/finished.json").decode("utf-8"))
            started_json = json.loads(download_single_file_from_gcs(bucket_name, new_source_prefix+"/started.json").decode("utf-8"))

            upload_ci_build_data(target_url, finished_json, started_json, triggered_name, triggered_email, triggered_id)

    # Respond with a 204 No Content status code once processing is complete
    return '', 204

# Based on https://docs.logilica.com/advanced/import/build-data
def upload_ci_build_data(details_url: str, finished_json: dict, started_json: dict, triggered_name: str, triggered_email: str, triggered_id: str):
    try:
        logilica_token = LOGILICA_TOKEN
        if not logilica_token:
            raise ValueError("LOGILICA_TOKEN environment variable is not set")
            
        logilica_domain = "redhat"
        headers = {
            "Content-Type": "application/json",
            "X-lgca-token": logilica_token,
            "x-lgca-domain": logilica_domain
        }
        
        # Get repository ID
        response = requests.get("https://logilica.io/api/import/v1/repositories", headers=headers)
        response.raise_for_status()
        
        repo_id = ""
        data = response.json()
        for repo in data:
            if repo["name"] == finished_json['metadata']['repo']:
                repo_id = repo["id"]
                break
                
        if not repo_id:
            raise ValueError(f"Repository {finished_json['metadata']['repo']} not found in Logilica")
            
        url = f"https://logilica.io/api/import/v1/ci_build/{repo_id}/create"
        details = details_url.split("/pull/")[1]
        original_id = details.split("/")[3]
        name_of_payload = details.split("/")[2]
        name_of_payload = "OpenShift CI " + name_of_payload.split("-")[-1]
        
        # Construct payload with actual data from the CI build
        payload = [{
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
                "lastActivity": 1
            },
            "status": "Completed",
            "conclusion": finished_json["result"].capitalize(),
            "repoUrl": "https://github.com/"+finished_json.get("metadata", {}).get("repo", "unknown"),
            "commit": started_json.get("repo-commit", "unknown"),
            "pullRequestUrls": [details_url],
            "isDeployment": True,
            "stages": [{
                "name": name_of_payload,
                "id": original_id,
                "url": details_url,
                "startedAt": started_json["timestamp"],
                "completedAt": finished_json["timestamp"],
                "status": "Completed",
                "conclusion": finished_json["result"].capitalize(),
                "jobs": [{
                    "name": name_of_payload,
                    "startedAt": started_json["timestamp"],
                    "completedAt": finished_json["timestamp"],
                    "status": "Completed",
                    "conclusion": finished_json["result"].capitalize()
                }]
            }]
        }]
        
        response = requests.post(url, headers=headers, json=payload)
        response.raise_for_status()
        print("Successfully uploaded CI build data to Logilica")
        
    except requests.exceptions.RequestException as e:
        print(f"Error making request to Logilica: {str(e)}")
        raise
    except Exception as e:
        print(f"Unexpected error in upload_ci_build_data: {str(e)}")
        raise


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5001)
