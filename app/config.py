import os
from pathlib import Path


def load_dotenv(path:str=".env") -> None:
    env_path = Path(path)
    if not env_path.exists():
        return
    
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key,value = line.split("=",1)
        os.environ.setdefault(key.strip(),value.strip().strip('"').strip('"'))

load_dotenv()


OPENAI_API_KEY = os.getenv("OPENAI_API_KEY","")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "deepseek-chat")


AMAP_WEB_SERVICE_KEY = os.getenv("AMAP_WEB_SERVICE_KEY", "")
CAMPUSLIFE_DINING_URL = os.getenv("CAMPUSLIFE_DINING_URL", "")

