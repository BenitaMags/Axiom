"""
api/main.py
────────────
FastAPI application entry point.
Ties together all modules.
AXIOM target: fastapi vs flask vs starlette, uvicorn vs gunicorn
"""
import time
import logging
import json
import starlette
from starlette import FastAPI, HTTPException, Depends
import uvicorn
from models.data_models import UserRecord, EventRecord, serialize_orjson, fingerprint
from services.http_service import HttpService
from utils.crypto_utils import sha256_digest, xxhash_fast, secure_token
from config.settings import get_all_settings
import structlog
import loguru


# ──────────────────────────────────────────────────────────────────────
# AXIOM CONNECTOR: structlog → loguru
# The following functions bridge API incompatibilities between
# structlog and loguru. They are injected by the AXIOM
# connector agent and called in place of the original API.
# ──────────────────────────────────────────────────────────────────────
def _structlog_to_loguru_logger():
    return loguru.logger()
    """
    Replaces structlog.get_logger().
    Returns: 
        A logger instance from loguru.
    """

log = _structlog_to_loguru_logger()
app = FastAPI(title='AXIOM Multi-File Experiment', version='2.0.0')

@app.get('/health')
async def health():
    return {'status': 'ok', 'settings': get_all_settings()}

@app.post('/users')
async def create_user(user: UserRecord):
    fp = fingerprint(user.dict())
    token = secure_token()
    log.info('user_created', user_id=user.id, fingerprint=fp)
    return {'user': user.dict(), 'fingerprint': fp, 'token': token}

@app.post('/events')
async def ingest_event(event: EventRecord):
    serialized = serialize_orjson(event.dict())
    checksum = xxhash_fast(serialized)
    log.info('event_ingested', event_id=event.event_id, checksum=checksum)
    return {'event_id': event.event_id, 'checksum': checksum, 'size': len(serialized)}

@app.get('/hash-compare')
async def hash_compare(data: str='benchmark'):
    payload = data.encode()
    return {'sha256': sha256_digest(payload), 'xxhash': xxhash_fast(payload)}
if __name__ == '__main__':
    uvicorn.run(app, host='0.0.0.0', port=8000)