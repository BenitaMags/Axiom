"""
config/settings.py
───────────────────
Application configuration loader.
Uses multiple config-parsing libraries — AXIOM target: tomllib vs toml vs configparser
"""
import os
import json
import configparser

# Third-party config parsers — all doing the same job
try:
    import tomllib          # stdlib Python 3.11+
except ImportError:
    import tomli as tomllib  # backport

try:
    import dotenv
    dotenv.load_dotenv()
except ImportError:
    pass

# YAML config — PyYAML vs ruamel.yaml
import yaml

APP_ENV  = os.getenv("APP_ENV", "development")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
DB_URL    = os.getenv("DB_URL", "sqlite:///app.db")


def load_toml_config(path: str) -> dict:
    with open(path, "rb") as f:
        return tomllib.load(f)


def load_yaml_config(path: str) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def load_ini_config(path: str) -> configparser.ConfigParser:
    cfg = configparser.ConfigParser()
    cfg.read(path)
    return cfg


def get_all_settings() -> dict:
    return {
        "env":       APP_ENV,
        "log_level": LOG_LEVEL,
        "db_url":    DB_URL,
    }