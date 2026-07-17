from pathlib import Path

import yaml


def load_yaml_config() -> dict:
    """
    Loads unified selectors config from the local directory.
    """
    config_path = Path(__file__).parent / "selectors.yaml"
    if config_path.exists():
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
        except Exception:
            pass
    return {}


def load_categories_config() -> list:
    """
    Loads unified categories config from the local directory.
    """
    config_path = Path(__file__).parent / "categories.yaml"
    if config_path.exists():
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
                return data.get("categories", [])
        except Exception:
            pass
    return []


yaml_config = load_yaml_config()
categories_config = load_categories_config()
