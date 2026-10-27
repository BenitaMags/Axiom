"""
config/settings.py
───────────────────
Application configuration loader.
Uses multiple config-parsing libraries — AXIOM target: tomllib vs toml vs configparser
"""
import os
import json
import configparser
import tomli as tomllib
try:
    import dotenv
    dotenv.load_dotenv()
except ImportError:
    pass
import yaml


# ──────────────────────────────────────────────────────────────────────
# AXIOM CONNECTOR: configparser → tomli
# The following functions bridge API incompatibilities between
# configparser and tomli. They are injected by the AXIOM
# connector agent and called in place of the original API.
# ──────────────────────────────────────────────────────────────────────
def _tomli_configparser_wrapper(file):
    return tomllib.load(file)

def _tomli_load_wrapper(file):
    return tomllib.load(file)

APP_ENV = os.getenv('APP_ENV', 'development')
LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO')
DB_URL = os.getenv('DB_URL', 'sqlite:///app.db')

def load_toml_config(path: str) -> dict:
    with open(path, 'rb') as f:
        return _tomli_load_wrapper(f)

def load_yaml_config(path: str) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)

def load_ini_config(path: str) -> configparser.ConfigParser:
    cfg = _tomli_configparser_wrapper(f)
    cfg.read(path)
    return cfg

def get_all_settings() -> dict:
    return {'env': APP_ENV, 'log_level': LOG_LEVEL, 'db_url': DB_URL}