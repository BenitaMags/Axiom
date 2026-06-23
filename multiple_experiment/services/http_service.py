"""
services/http_service.py
─────────────────────────
HTTP client service layer.
AXIOM target: requests vs httpx vs aiohttp
"""
import json
import time
import logging
from typing import Any

import requests
import httpx
import urllib3

logger = logging.getLogger(__name__)


class HttpService:
    """Synchronous HTTP client — requests vs httpx."""

    def __init__(self, base_url: str, timeout: int = 10):
        self.base_url = base_url.rstrip("/")
        self.timeout  = timeout
        self.session  = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})

    def get(self, path: str, params: dict | None = None) -> dict:
        url = f"{self.base_url}/{path.lstrip('/')}"
        resp = self.session.get(url, params=params, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    def post(self, path: str, payload: dict) -> dict:
        url = f"{self.base_url}/{path.lstrip('/')}"
        resp = self.session.post(url, json=payload, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    def close(self):
        self.session.close()


class AsyncHttpService:
    """Async HTTP client — httpx async."""

    def __init__(self, base_url: str, timeout: int = 10):
        self.base_url = base_url.rstrip("/")
        self.client   = httpx.AsyncClient(timeout=timeout)

    async def get(self, path: str, params: dict | None = None) -> dict:
        url  = f"{self.base_url}/{path.lstrip('/')}"
        resp = await self.client.get(url, params=params)
        resp.raise_for_status()
        return resp.json()

    async def post(self, path: str, payload: dict) -> dict:
        url  = f"{self.base_url}/{path.lstrip('/')}"
        resp = await self.client.post(url, json=payload)
        resp.raise_for_status()
        return resp.json()

    async def close(self):
        await self.client.aclose()


def low_level_get(url: str) -> bytes:
    """urllib3 direct pool for bulk/internal requests."""
    http = urllib3.PoolManager()
    r = http.request("GET", url)
    return r.data


def batch_fetch(urls: list[str]) -> list[dict]:
    results = []
    with requests.Session() as s:
        for url in urls:
            try:
                r = s.get(url, timeout=5)
                results.append({"url": url, "status": r.status_code, "ok": r.ok})
            except requests.RequestException as e:
                results.append({"url": url, "error": str(e)})
    return results