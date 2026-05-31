"""
experiment_libs/http_experiment.py
────────────────────────────────────
Experiment file: HTTP clients

A realistic data-fetching script that a developer wrote while hedging
between requests and httpx — both are imported, but every actual call
uses the requests module-level API (requests.get / requests.post).
httpx is imported "just in case" but never used.

AXIOM should detect:
  - requests ≡ httpx  (same role: sync HTTP GET/POST with JSON, headers, timeout)
  - httpx is redundant dead import
  - Winner: httpx (faster import on most machines, modern, drop-in for .get/.post)
  - AST rewrite: `import requests` → `import httpx`, call sites rewritten

Expected AXIOM output:
  Role      : sync HTTP client
  Original  : requests
  Winner    : httpx        (or requests if benchmarks flip — both valid)
  Confidence: HIGH         (one package does ALL the work, clear winner)
  Patch     : import line replaced, dead import removed
"""

import requests
import httpx


API_BASE   = "https://jsonplaceholder.typicode.com"
USER_AGENT = "AXIOM-DataFetcher/1.0"
TIMEOUT    = 8


# ── All HTTP calls go through requests ────────────────────────────────────────
# httpx is imported above but never actually called anywhere in this file.

def get_user(user_id: int) -> dict:
    """Fetch a single user record."""
    resp = requests.get(
        f"{API_BASE}/users/{user_id}",
        headers={"User-Agent": USER_AGENT},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


def list_posts(user_id: int) -> list[dict]:
    """Fetch all posts belonging to a user."""
    resp = requests.get(
        f"{API_BASE}/posts",
        params={"userId": user_id},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


def create_post(title: str, body: str, user_id: int) -> dict:
    """Create a new post via POST."""
    resp = requests.post(
        f"{API_BASE}/posts",
        json={"title": title, "body": body, "userId": user_id},
        headers={"Content-Type": "application/json"},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


def update_post(post_id: int, title: str) -> dict:
    """Partially update a post via PATCH."""
    resp = requests.patch(
        f"{API_BASE}/posts/{post_id}",
        json={"title": title},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


def delete_post(post_id: int) -> bool:
    """Delete a post. Returns True on success."""
    resp = requests.delete(
        f"{API_BASE}/posts/{post_id}",
        timeout=TIMEOUT,
    )
    return resp.status_code == 200


def fetch_comments(post_id: int) -> list[dict]:
    """Fetch all comments on a post."""
    resp = requests.get(
        f"{API_BASE}/comments",
        params={"postId": post_id},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


# ── Main ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    user  = get_user(1)
    print(f"User  : {user['name']} ({user['email']})")

    posts = list_posts(1)
    print(f"Posts : {len(posts)} found")

    new   = create_post("Hello from AXIOM", "Optimized import pipeline.", user_id=1)
    print(f"Created post id={new['id']}")

    comments = fetch_comments(posts[0]["id"])
    print(f"Comments on post {posts[0]['id']}: {len(comments)}")