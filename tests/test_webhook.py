import json
import hmac
import hashlib
import os
from app import app

# Use the same test secret that's set in test_app.py
# (which sets os.environ['GITHUB_SECRET'] = 'test-secret')
SECRET = os.environ.get("GITHUB_SECRET", "test-secret")


def generate_signature(payload):
    return (
        "sha1="
        + hmac.new(
            SECRET.encode(), json.dumps(payload).encode(), hashlib.sha1
        ).hexdigest()
    )


def test_webhook():
    client = app.test_client()
    payload = {"zen": "Keep it simple"}
    signature = generate_signature(payload)

    response = client.post(
        "/webhook",
        json=payload,
        headers={"X-Hub-Signature": signature, "X-GitHub-Event": "ping"},
    )
    assert response.status_code == 200

    # Check raw data if json method fails
    try:
        result = response.get_json()
        assert result == {"msg": "Pong!"}
    except:
        data = json.loads(response.data.decode("utf-8"))
        assert data == {"msg": "Pong!"}
