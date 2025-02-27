
from google.cloud import storage
from flask import Flask, request, abort
import hmac
import hashlib
import json
import os

app = Flask(__name__)

GITHUB_SECRET = os.getenv('GITHUB_SECRET')
CHECK_RUN_NAME = 'codecov/patch'


def download_directory_from_gcs(bucket_name, source_prefix, destination_directory):
    """Downloads a directory from Google Cloud Storage.

    Args:
        bucket_name: The name of the GCS bucket.
        source_prefix: The GCS prefix (directory) to download.
        destination_directory: The local directory to download the files to.
    """

    storage_client = storage.Client.create_anonymous_client()
    bucket = storage_client.bucket(bucket_name)
    blobs = bucket.list_blobs(prefix=source_prefix)  # Get list of files in the directory

    os.makedirs(destination_directory, exist_ok=True)  # Create destination directory if it doesn't exist

    for blob in blobs:
        if blob.name.endswith('/'): #skip directories
            continue
        relative_path = os.path.relpath(blob.name, source_prefix)
        destination_file_path = os.path.join(destination_directory, relative_path)
        os.makedirs(os.path.dirname(destination_file_path), exist_ok=True) #create subdirectories
        blob.download_to_filename(destination_file_path)
        print(f"Downloaded {blob.name} to {destination_file_path}")

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

    if event == 'check_run':
        check_run_name = payload['check_run']['name']
        if check_run_name == CHECK_RUN_NAME:
            details_url = payload['check_run']['details_url']
            print(f"Downloading logs from {details_url}")
            details_url = "https://prow.ci.openshift.org/view/gs/test-platform-results/pr-logs/pull/codeready-toolchain_host-operator/1145/pull-ci-codeready-toolchain-host-operator-master-e2e/1891795860363153408" 
            bucket_name = "test-platform-results"
            source_prefix = details_url.split("/gs/")[-1]
            destination_directory = details_url.split("/")[-1]
            download_directory_from_gcs(bucket_name, source_prefix, destination_directory)

    # Respond with a 204 No Content status code once processing is complete
    return '', 204

if __name__ == '__main__':
    # Run the Flask app
    app.run(debug=True, host='0.0.0.0', port=5001)
