import requests
from typing import Any


class HttpReporter:
    def __init__(self, base_url: str, timeout: int = 10) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout  = timeout
        self._session = requests.Session()
        self._session.headers.update({"Content-Type": "application/json"})

    def post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        resp = self._session.post(
            f"{self.base_url}/{path.lstrip('/')}",
            json=payload,
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.json()

    def get(self, path: str) -> dict[str, Any]:
        resp = self._session.get(
            f"{self.base_url}/{path.lstrip('/')}",
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.json()

    def close(self) -> None:
        self._session.close()

    def __enter__(self) -> "HttpReporter":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()
