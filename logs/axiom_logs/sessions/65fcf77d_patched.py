import requests
import httpx

def fetch(url: str) -> dict:
    response = requests.get(url, timeout=5)
    return response.json()

def fetch_async(url: str) -> dict:
    with httpx.Client() as client:
        return client.get(url).json()
