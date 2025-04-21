import pytest
from flask import Flask, json
from unittest.mock import patch, MagicMock
import hmac
import hashlib
import os
import requests

# Set environment variables for testing
os.environ['GITHUB_SECRET'] = 'test-secret'
os.environ['LOGILICA_TOKEN'] = 'test-logilica-token'

# Import the Flask app after setting environment variables
from app import app as flask_app, verify_signature

@pytest.fixture
def app():
    """Create and configure a new app instance for each test."""
    flask_app.config.update({
        "TESTING": True,
    })
    yield flask_app

@pytest.fixture
def client(app):
    """A test client for the app."""
    return app.test_client()

# --- Tests for verify_signature ---

def generate_signature(payload, secret):
    """Helper function to generate a valid HMAC signature."""
    mac = hmac.new(secret.encode('utf-8'), msg=payload, digestmod=hashlib.sha1)
    return f"sha1={mac.hexdigest()}"

def test_verify_signature_valid():
    """Test verify_signature with a correct signature."""
    secret = 'test-secret'
    payload = b'{"test": "payload"}'
    signature = generate_signature(payload, secret)
    assert verify_signature(payload, signature) is True

def test_verify_signature_invalid():
    """Test verify_signature with an incorrect signature."""
    secret = 'test-secret'
    payload = b'{"test": "payload"}'
    invalid_signature = "sha1=invalid_signature_hash"
    assert verify_signature(payload, invalid_signature) is False

def test_verify_signature_missing_header():
    """Test verify_signature when the header is missing."""
    payload = b'{"test": "payload"}'
    assert verify_signature(payload, None) is False

def test_verify_signature_incorrect_sha_method():
    """Test verify_signature with a non-sha1 method."""
    secret = 'test-secret'
    payload = b'{"test": "payload"}'
    signature = generate_signature(payload, secret)
    # Simulate a header with sha256 instead of sha1
    incorrect_method_signature = signature.replace("sha1=", "sha256=")
    assert verify_signature(payload, incorrect_method_signature) is False

def test_verify_signature_value_error():
    """Test verify_signature with an incorrectly formatted header."""
    secret = 'test-secret'
    payload = b'{"test": "payload"}'
    malformed_signature = "invalidformat"
    assert verify_signature(payload, malformed_signature) is False

# --- Tests for download_single_file_from_gcs ---

@patch('app.storage.Client')
def test_download_single_file_from_gcs_success(mock_storage_client):
    """Test successful download from GCS."""
    # Arrange
    mock_blob = MagicMock()
    mock_blob.download_as_bytes.return_value = b'test content'
    mock_bucket = MagicMock()
    mock_bucket.blob.return_value = mock_blob
    mock_storage_client.create_anonymous_client.return_value.bucket.return_value = mock_bucket

    # Act
    from app import download_single_file_from_gcs
    result = download_single_file_from_gcs('test-bucket', 'test-blob.json')

    # Assert
    assert result == b'test content'
    mock_storage_client.create_anonymous_client.assert_called_once()
    mock_storage_client.create_anonymous_client.return_value.bucket.assert_called_once_with('test-bucket')
    mock_bucket.blob.assert_called_once_with('test-blob.json')
    mock_blob.download_as_bytes.assert_called_once()

@patch('app.storage.Client')
def test_download_single_file_from_gcs_error(mock_storage_client):
    """Test error during download from GCS."""
    # Arrange
    mock_storage_client.create_anonymous_client.return_value.bucket.side_effect = Exception("GCS Error")

    # Act & Assert
    from app import download_single_file_from_gcs
    with pytest.raises(Exception, match="GCS Error"):
        download_single_file_from_gcs('test-bucket', 'test-blob.json')

    mock_storage_client.create_anonymous_client.assert_called_once()
    mock_storage_client.create_anonymous_client.return_value.bucket.assert_called_once_with('test-bucket')

# --- Tests for /webhook endpoint ---

def test_webhook_ping(client):
    """Test the webhook ping event."""
    headers = {
        'X-GitHub-Event': 'ping',
        'X-Hub-Signature': 'sha1=dummy_signature' # Signature validation is skipped for ping
    }
    response = client.post('/webhook', headers=headers, json={})
    assert response.status_code == 200
    
    # Check raw data if json attribute is None
    if response.json is None:
        import json
        data = json.loads(response.data.decode('utf-8'))
        assert data == {'msg': 'Pong!'}
    else:
        assert response.json == {'msg': 'Pong!'}

def test_webhook_missing_signature(client):
    """Test webhook request with missing signature header."""
    headers = {
        'X-GitHub-Event': 'push' # Any event other than ping
    }
    response = client.post('/webhook', headers=headers, data=b'{}', content_type='application/json')
    assert response.status_code == 400
    assert b'Signature missing' in response.data

@patch('app.verify_signature', return_value=False)
def test_webhook_invalid_signature(mock_verify_signature, client):
    """Test webhook request with an invalid signature."""
    payload = {'test': 'payload'}
    payload_bytes = json.dumps(payload).encode('utf-8')
    headers = {
        'X-GitHub-Event': 'push',
        'X-Hub-Signature': 'sha1=invalid_signature'
    }
    response = client.post('/webhook', headers=headers, data=payload_bytes, content_type='application/json')
    assert response.status_code == 400
    assert b'Invalid signature' in response.data
    mock_verify_signature.assert_called_once_with(payload_bytes, 'sha1=invalid_signature')

@patch('app.upload_ci_build_data')
@patch('app.download_single_file_from_gcs')
@patch('app.verify_signature', return_value=True)
def test_webhook_status_success(mock_verify_sig, mock_download_gcs, mock_upload_ci, client):
    """Test successful handling of a status event (success state)."""
    # Mock GCS downloads
    finished_json = {"timestamp": 1678886400, "result": "SUCCESS", "metadata": {"repo": "test-org/test-repo"}}
    started_json = {"timestamp": 1678886300, "repo-commit": "abcdef123456"}
    mock_download_gcs.side_effect = [
        json.dumps(finished_json).encode('utf-8'),
        json.dumps(started_json).encode('utf-8')
    ]

    # Payload for the status event
    payload = {
        'context': 'ci/prow/e2e',
        'state': 'success',
        'commit': {
            'commit': {'author': {'name': 'Test User', 'email': 'test@example.com'}},
            'author': {'login': 'testuser'}
        },
        'target_url': 'https://gcsweb/gs/test-platform-results/pr-logs/pull/123/e2e-test/456'
    }
    payload_bytes = json.dumps(payload).encode('utf-8')
    signature = generate_signature(payload_bytes, 'test-secret')
    headers = {
        'X-GitHub-Event': 'status',
        'X-Hub-Signature': signature
    }

    response = client.post('/webhook', headers=headers, data=payload_bytes, content_type='application/json')

    assert response.status_code == 204
    mock_verify_sig.assert_called_once_with(payload_bytes, signature)
    assert mock_download_gcs.call_count == 2
    mock_download_gcs.assert_any_call('test-platform-results', 'pr-logs/pull/123/e2e-test/456/finished.json')
    mock_download_gcs.assert_any_call('test-platform-results', 'pr-logs/pull/123/e2e-test/456/started.json')
    mock_upload_ci.assert_called_once_with(
        payload['target_url'], finished_json, started_json, 'Test User', 'test@example.com', 'testuser'
    )

@patch('app.upload_ci_build_data')
@patch('app.download_single_file_from_gcs')
@patch('app.verify_signature', return_value=True)
def test_webhook_status_failure_state(mock_verify_sig, mock_download_gcs, mock_upload_ci, client):
    """Test successful handling of a status event (failure state)."""
    # Mock GCS downloads
    finished_json = {"timestamp": 1678886400, "result": "FAILURE", "metadata": {"repo": "test-org/test-repo"}}
    started_json = {"timestamp": 1678886300, "repo-commit": "abcdef123456"}
    mock_download_gcs.side_effect = [
        json.dumps(finished_json).encode('utf-8'),
        json.dumps(started_json).encode('utf-8')
    ]

    # Payload for the status event
    payload = {
        'context': 'ci/prow/e2e',
        'state': 'failure', # Testing failure state
        'commit': {
            'commit': {'author': {'name': 'Test User', 'email': 'test@example.com'}},
            'author': {'login': 'testuser'}
        },
        'target_url': 'https://gcsweb/gs/test-platform-results/pr-logs/pull/123/e2e-test/456'
    }
    payload_bytes = json.dumps(payload).encode('utf-8')
    signature = generate_signature(payload_bytes, 'test-secret')
    headers = {
        'X-GitHub-Event': 'status',
        'X-Hub-Signature': signature
    }

    response = client.post('/webhook', headers=headers, data=payload_bytes, content_type='application/json')

    assert response.status_code == 204
    mock_verify_sig.assert_called_once_with(payload_bytes, signature)
    assert mock_download_gcs.call_count == 2
    mock_upload_ci.assert_called_once_with(
        payload['target_url'], finished_json, started_json, 'Test User', 'test@example.com', 'testuser'
    )

@patch('app.download_single_file_from_gcs')
@patch('app.verify_signature', return_value=True)
def test_webhook_status_ignored_context(mock_verify_sig, mock_download_gcs, client):
    """Test status event with an ignored context."""
    payload = {
        'context': 'ci/prow/other-test', # Different context
        'state': 'success',
        'commit': {'commit': {'author': {}}, 'author': {}},
        'target_url': 'some_url'
    }
    payload_bytes = json.dumps(payload).encode('utf-8')
    signature = generate_signature(payload_bytes, 'test-secret')
    headers = {
        'X-GitHub-Event': 'status',
        'X-Hub-Signature': signature
    }

    response = client.post('/webhook', headers=headers, data=payload_bytes, content_type='application/json')

    assert response.status_code == 204
    mock_verify_sig.assert_called_once_with(payload_bytes, signature)
    mock_download_gcs.assert_not_called() # Should not attempt download

@patch('app.download_single_file_from_gcs')
@patch('app.verify_signature', return_value=True)
def test_webhook_status_ignored_state(mock_verify_sig, mock_download_gcs, client):
    """Test status event with an ignored state (e.g., pending)."""
    payload = {
        'context': 'ci/prow/e2e',
        'state': 'pending', # Ignored state
        'commit': {'commit': {'author': {}}, 'author': {}},
        'target_url': 'some_url'
    }
    payload_bytes = json.dumps(payload).encode('utf-8')
    signature = generate_signature(payload_bytes, 'test-secret')
    headers = {
        'X-GitHub-Event': 'status',
        'X-Hub-Signature': signature
    }

    response = client.post('/webhook', headers=headers, data=payload_bytes, content_type='application/json')

    assert response.status_code == 204
    mock_verify_sig.assert_called_once_with(payload_bytes, signature)
    mock_download_gcs.assert_not_called() # Should not attempt download

@patch('app.time.sleep') # Mock time.sleep to speed up retry test
@patch('app.upload_ci_build_data')
@patch('app.download_single_file_from_gcs')
@patch('app.verify_signature', return_value=True)
def test_webhook_status_upload_retry(mock_verify_sig, mock_download_gcs, mock_upload_ci, mock_sleep, client):
    """Test retry logic when upload_ci_build_data fails initially."""
    # Mock GCS downloads
    finished_json = {"timestamp": 1678886400, "result": "SUCCESS", "metadata": {"repo": "test-org/test-repo"}}
    started_json = {"timestamp": 1678886300, "repo-commit": "abcdef123456"}
    mock_download_gcs.side_effect = [
        json.dumps(finished_json).encode('utf-8'),
        json.dumps(started_json).encode('utf-8')
    ]

    # Mock upload_ci_build_data to fail twice then succeed
    mock_upload_ci.side_effect = [
        requests.exceptions.RequestException("Network Error"),
        requests.exceptions.RequestException("Another Error"),
        None # Success on the third attempt
    ]

    payload = {
        'context': 'ci/prow/e2e',
        'state': 'success',
        'commit': {
            'commit': {'author': {'name': 'Retry User', 'email': 'retry@example.com'}},
            'author': {'login': 'retryuser'}
        },
        'target_url': 'https://gcsweb/gs/test-platform-results/pr-logs/pull/789/e2e-retry/101'
    }
    payload_bytes = json.dumps(payload).encode('utf-8')
    signature = generate_signature(payload_bytes, 'test-secret')
    headers = {
        'X-GitHub-Event': 'status',
        'X-Hub-Signature': signature
    }

    response = client.post('/webhook', headers=headers, data=payload_bytes, content_type='application/json')

    assert response.status_code == 204
    assert mock_upload_ci.call_count == 3
    assert mock_sleep.call_count == 2 # Should sleep twice before succeeding

@patch('app.time.sleep') # Mock time.sleep
@patch('app.upload_ci_build_data', side_effect=requests.exceptions.RequestException("Persistent Error"))
@patch('app.download_single_file_from_gcs')
@patch('app.verify_signature', return_value=True)
def test_webhook_status_upload_retry_fails(mock_verify_sig, mock_download_gcs, mock_upload_ci, mock_sleep, client):
    """Test when upload_ci_build_data fails all retry attempts."""
    # Mock GCS downloads
    finished_json = {"timestamp": 1678886400, "result": "SUCCESS", "metadata": {"repo": "test-org/test-repo"}}
    started_json = {"timestamp": 1678886300, "repo-commit": "abcdef123456"}
    mock_download_gcs.side_effect = [
        json.dumps(finished_json).encode('utf-8'),
        json.dumps(started_json).encode('utf-8')
    ]

    payload = {
        'context': 'ci/prow/e2e',
        'state': 'success',
        'commit': {
            'commit': {'author': {'name': 'Fail User', 'email': 'fail@example.com'}},
            'author': {'login': 'failuser'}
        },
        'target_url': 'https://gcsweb/gs/test-platform-results/pr-logs/pull/000/e2e-fail/111'
    }
    payload_bytes = json.dumps(payload).encode('utf-8')
    signature = generate_signature(payload_bytes, 'test-secret')
    headers = {
        'X-GitHub-Event': 'status',
        'X-Hub-Signature': signature
    }

    # Expect the exception to be raised after all retries
    with pytest.raises(requests.exceptions.RequestException, match="Persistent Error"):
        client.post('/webhook', headers=headers, data=payload_bytes, content_type='application/json')

    assert mock_upload_ci.call_count == 7 # Max retries
    assert mock_sleep.call_count == 6 # Sleeps between attempts

# --- Tests for upload_ci_build_data ---

@patch('app.requests.post')
@patch('app.requests.get')
def test_upload_ci_build_data_success(mock_get, mock_post):
    """Test successful upload of CI build data."""
    # Arrange
    details_url = "https://gcsweb/gs/test-platform-results/pr-logs/pull/openshift/repo-name/123/pull-ci-repo-name-job-name/456"
    finished_json = {
        "timestamp": 1678886400,
        "result": "SUCCESS",
        "metadata": {"repo": "openshift/repo-name"}
    }
    started_json = {
        "timestamp": 1678886300,
        "repo-commit": "abcdef123456"
    }
    triggered_name = "Test User"
    triggered_email = "test@example.com"
    triggered_id = "testuser"

    # Mock requests.get response (finding the repo ID)
    mock_get_response = MagicMock()
    mock_get_response.raise_for_status.return_value = None
    mock_get_response.json.return_value = [
        {"id": "repo1", "name": "other/repo"},
        {"id": "repo-abc", "name": "openshift/repo-name"}
    ]
    mock_get.return_value = mock_get_response

    # Mock requests.post response (uploading data)
    mock_post_response = MagicMock()
    mock_post_response.raise_for_status.return_value = None
    mock_post.return_value = mock_post_response

    expected_headers = {
        "Content-Type": "application/json",
        "X-lgca-token": "test-logilica-token",
        "x-lgca-domain": "redhat"
    }
    expected_upload_url = "https://logilica.io/api/import/v1/ci_build/repo-abc/create"
    expected_payload = [{
        "origin": "OpenShift_CI",
        "originalID": "pull-ci-repo-name-job-name",
        "name": "OpenShift CI 123",
        "url": details_url,
        "startedAt": 1678886300,
        "createdAt": 1678886300,
        "completedAt": 1678886400,
        "triggeredBy": {
            "name": triggered_name,
            "email": triggered_email,
            "accountId": triggered_id,
            "lastActivity": 1
        },
        "status": "Completed",
        "conclusion": "Success",
        "repoUrl": "https://github.com/openshift/repo-name",
        "commit": "abcdef123456",
        "pullRequestUrls": [details_url],
        "isDeployment": True,
        "stages": [{
            "name": "OpenShift CI 123",
            "id": "pull-ci-repo-name-job-name",
            "url": details_url,
            "startedAt": 1678886300,
            "completedAt": 1678886400,
            "status": "Completed",
            "conclusion": "Success",
            "jobs": [{
                "name": "OpenShift CI 123",
                "startedAt": 1678886300,
                "completedAt": 1678886400,
                "status": "Completed",
                "conclusion": "Success"
            }]
        }]
    }]

    # Act
    from app import upload_ci_build_data
    upload_ci_build_data(details_url, finished_json, started_json, triggered_name, triggered_email, triggered_id)

    # Assert
    mock_get.assert_called_once_with("https://logilica.io/api/import/v1/repositories", headers=expected_headers)
    mock_post.assert_called_once_with(expected_upload_url, headers=expected_headers, json=expected_payload)
    mock_get_response.raise_for_status.assert_called_once()
    mock_post_response.raise_for_status.assert_called_once()

@patch('app.requests.post')
@patch('app.requests.get')
def test_upload_ci_build_data_repo_not_found(mock_get, mock_post):
    """Test upload when the target repository is not found in Logilica."""
    # Arrange
    details_url = "https://gcsweb/gs/test-platform-results/pr-logs/pull/openshift/repo-name/123/pull-ci-repo-name-job-name/456"
    finished_json = {"timestamp": 1678886400, "result": "SUCCESS", "metadata": {"repo": "openshift/repo-name"}}
    started_json = {"timestamp": 1678886300, "repo-commit": "abcdef123456"}
    # Mock requests.get response (repo not found)
    mock_get_response = MagicMock()
    mock_get_response.raise_for_status.return_value = None
    mock_get_response.json.return_value = [
        {"id": "repo1", "name": "other/repo"},
        {"id": "repo2", "name": "another/repo"}
    ]
    mock_get.return_value = mock_get_response

    # Act & Assert
    from app import upload_ci_build_data
    with pytest.raises(ValueError, match="Repository openshift/repo-name not found in Logilica"):
        upload_ci_build_data(details_url, finished_json, started_json, "n", "e", "i")

    mock_get.assert_called_once()
    mock_post.assert_not_called()

@patch('app.requests.post')
@patch('app.requests.get', side_effect=requests.exceptions.RequestException("GET Error"))
def test_upload_ci_build_data_get_request_error(mock_get, mock_post):
    """Test upload when getting repositories fails."""
    # Arrange
    details_url = "url"
    finished_json = {"metadata": {"repo": "r"}}
    started_json = {}

    # Act & Assert
    from app import upload_ci_build_data
    with pytest.raises(requests.exceptions.RequestException, match="GET Error"):
        upload_ci_build_data(details_url, finished_json, started_json, "n", "e", "i")

    mock_get.assert_called_once()
    mock_post.assert_not_called()

@patch('app.requests.post', side_effect=requests.exceptions.RequestException("POST Error"))
@patch('app.requests.get')
def test_upload_ci_build_data_post_request_error(mock_get, mock_post):
    """Test upload when posting data fails."""
    # Arrange
    details_url = "https://gcsweb/gs/test-platform-results/pr-logs/pull/openshift/repo-name/123/pull-ci-repo-name-job-name/456"
    finished_json = {"timestamp": 0, "result": "S", "metadata": {"repo": "openshift/repo-name"}}
    started_json = {"timestamp": 0, "repo-commit": "c"}
    # Mock requests.get response (finding the repo ID)
    mock_get_response = MagicMock()
    mock_get_response.raise_for_status.return_value = None
    mock_get_response.json.return_value = [{"id": "repo-abc", "name": "openshift/repo-name"}]
    mock_get.return_value = mock_get_response

    # Act & Assert
    from app import upload_ci_build_data
    with pytest.raises(requests.exceptions.RequestException, match="POST Error"):
        upload_ci_build_data(details_url, finished_json, started_json, "n", "e", "i")

    mock_get.assert_called_once()
    mock_post.assert_called_once() # Post is attempted

def test_upload_ci_build_data_missing_token(monkeypatch):
    """Test upload when LOGILICA_TOKEN is not set."""
    # Arrange
    monkeypatch.delenv("LOGILICA_TOKEN", raising=False)
    details_url = "url"
    finished_json = {"metadata": {}}
    started_json = {}

    # Act & Assert
    # Need to re-import the function *after* env var is deleted
    # This is a bit tricky because it's defined at module level
    # A better approach might be to pass the token into the function
    # or use a class-based structure where dependencies are injected.
    # For now, we test the check within the function.
    with pytest.raises(ValueError, match="LOGILICA_TOKEN environment variable is not set"):
        # Directly calling the function to bypass module-level loading issues in test
        from app import upload_ci_build_data
        # We need to reload the module or the function to see the change in env var
        import importlib
        import app as app_module
        importlib.reload(app_module)
        app_module.upload_ci_build_data(details_url, finished_json, started_json, "n", "e", "i")

    # Restore token for other tests if needed (pytest fixtures handle this better)
    os.environ['LOGILICA_TOKEN'] = 'test-logilica-token'
    # Reload again to restore the original state for subsequent tests
    import importlib
    import app as app_module
    importlib.reload(app_module) 