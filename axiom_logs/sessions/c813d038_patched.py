"""
experiment_libs/http_experiment.py
────────────────────────────────────
Experiment file: HTTP clients

Tests AXIOM's ability to detect that requests and httpx
serve the same functional role (HTTP client) and pick the faster one.

Packages involved:
  - requests  : synchronous HTTP, most popular, slower to import (~40ms)
  - httpx     : modern async/sync HTTP, faster, drop-in compatible for GET/POST
  - urllib3   : low-level HTTP, requests is built on top of it

AXIOM should detect: requests ≡ httpx (same role, overlapping API)
Expected winner: httpx (faster import, modern, drop-in compatible)
"""

import requests
import httpx
from urllib3 import PoolManager


# ── Using requests ─────────────────────────────────────────────────────────────

def fetch_user_requests(user_id: int) -> dict:
    """Fetch a user record using requests library."""
    response = requests.get(
        f"https://jsonplaceholder.typicode.com/users/{user_id}",
        timeout=5,
    )
    response.raise_for_status()
    return response.json()


def post_data_requests(url: str, payload: dict) -> dict:
    """POST data using requests."""
    response = requests.post(url, json=payload, timeout=5)
    return response.json()


def fetch_with_headers_requests(url: str) -> str:
    """GET with custom headers using requests."""
    headers = {"User-Agent": "AXIOM-Experiment/1.0"}
    r = requests.get(url, headers=headers, timeout=5)
    return r.text


# ── Using httpx ────────────────────────────────────────────────────────────────

def fetch_user_httpx(user_id: int) -> dict:
    """Fetch a user record using httpx library."""
    with httpx.Client(timeout=5) as client:
        response = client.get(
            f"https://jsonplaceholder.typicode.com/users/{user_id}"
        )
        response.raise_for_status()
        return response.json()


def post_data_httpx(url: str, payload: dict) -> dict:
    """POST data using httpx."""
    with httpx.Client(timeout=5) as client:
        response = client.post(url, json=payload)
        return response.json()


# ── Using urllib3 ──────────────────────────────────────────────────────────────

def fetch_raw_urllib3(url: str) -> str:
    """Low-level GET using urllib3."""
    http = PoolManager()
    r = http.request("GET", url)
    return r.data.decode("utf-8")


# ── Main ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("Testing HTTP experiment...")
    user = fetch_user_requests(1)
    print(f"requests → name: {user['name']}")

    user2 = fetch_user_httpx(1)
    print(f"httpx    → name: {user2['name']}")