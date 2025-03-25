import json
import hmac
import hashlib
from app import app

SECRET = "your_secret_here"

def generate_signature(payload):
    return "sha1=" + hmac.new(SECRET.encode(), json.dumps(payload).encode(), hashlib.sha1).hexdigest()

def test_webhook():
    client = app.test_client()
    payload = {"zen": "Keep it simple"}
    signature = generate_signature(payload)

    response = client.post("/webhook", json=payload, headers={"X-Hub-Signature": signature})
    assert response.status_code == 200
    assert response.get_json() == {"msg": "Pong!"}
