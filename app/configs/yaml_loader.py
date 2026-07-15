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


yaml_config = load_yaml_config()
