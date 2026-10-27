"""
models/data_models.py
──────────────────────
Data validation and serialization.
AXIOM target: pydantic vs attrs vs dataclasses, msgpack vs pickle
"""
import json
import pickle
import hashlib
from dataclasses import dataclass, field, asdict
from typing import Optional

# Data validation — pydantic vs attrs
import pydantic
from pydantic import BaseModel, Field, validator

# Serialization — msgpack vs pickle vs orjson
import msgpack
import orjson


class UserRecord(BaseModel):
    id:         int
    username:   str
    email:      str
    score:      float = 0.0
    tags:       list[str] = Field(default_factory=list)
    metadata:   dict = Field(default_factory=dict)

    @validator("email")
    def email_must_contain_at(cls, v):
        if "@" not in v:
            raise ValueError("invalid email")
        return v


class EventRecord(BaseModel):
    event_id:   str
    user_id:    int
    event_type: str
    payload:    dict
    timestamp:  float


def serialize_msgpack(obj: dict) -> bytes:
    return msgpack.packb(obj, use_bin_type=True)


def deserialize_msgpack(data: bytes) -> dict:
    return msgpack.unpackb(data, raw=False)


def serialize_orjson(obj: dict) -> bytes:
    return orjson.dumps(obj)


def deserialize_orjson(data: bytes) -> dict:
    return orjson.loads(data)


def serialize_pickle(obj) -> bytes:
    return pickle.dumps(obj)


def fingerprint(record: dict) -> str:
    blob = orjson.dumps(record, option=orjson.OPT_SORT_KEYS)
    return hashlib.sha256(blob).hexdigest()