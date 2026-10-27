"""
config/settings.py
───────────────────
Application configuration loader.
Uses multiple config-parsing libraries — AXIOM target: tomllib vs toml vs configparser
"""
import os
import json
import tomli
import tomli as tomllib
try:
    import dotenv
    dotenv.load_dotenv()
except ImportError:
    pass
import yaml
APP_ENV = os.getenv('APP_ENV', 'development')
LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO')
DB_URL = os.getenv('DB_URL', 'sqlite:///app.db')

def load_toml_config(path: str) -> dict:
    with open(path, 'rb') as f:
        return tomli.load(f)

def load_yaml_config(path: str) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)

def load_ini_config(path: str) -> tomli.ConfigParser:
    cfg = tomli.ConfigParser()
    cfg.read(path)
    return cfg

def get_all_settings() -> dict:
    return {'env': APP_ENV, 'log_level': LOG_LEVEL, 'db_url': DB_URL}