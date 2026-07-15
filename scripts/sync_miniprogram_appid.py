from __future__ import annotations

import json
import os
from pathlib import Path


PROJECT_PRIVATE_CONFIG = Path("miniprogram/project.private.config.json")
ENV_PATH = Path(".env")


def load_env_value(key: str) -> str | None:
    value = os.getenv(key)
    if value:
        return value

    if not ENV_PATH.exists():
        return None

    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue

        name, raw_value = line.split("=", 1)
        if name.strip() == key:
            return raw_value.strip().strip('"').strip("'") or None

    return None


def main() -> None:
    appid = load_env_value("MINIPROGRAM_APPID")
    if not appid:
        raise SystemExit("Missing MINIPROGRAM_APPID in environment or .env")

    PROJECT_PRIVATE_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "appid": appid,
        "setting": {
            "urlCheck": False,
        },
    }

    with PROJECT_PRIVATE_CONFIG.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)
        file.write("\n")

    print(f"Updated {PROJECT_PRIVATE_CONFIG}")


if __name__ == "__main__":
    main()
