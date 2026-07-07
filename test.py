import httpx

from app.config import OPENAI_API_KEY, OPENAI_BASE_URL, OPENAI_MODEL

url = f"{OPENAI_BASE_URL.rstrip('/')}/chat/completions"

print("URL:", url)
print("MODEL:", OPENAI_MODEL)
print("KEY LENGTH:", len(OPENAI_API_KEY))
print("KEY PREFIX:", OPENAI_API_KEY[:6])
print("KEY SUFFIX:", OPENAI_API_KEY[-4:])

response = httpx.post(
    url,
    headers={
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    },
    json={
        "model": OPENAI_MODEL,
        "messages": [
            {"role": "user", "content": "你好"}
        ],
        "temperature": 0.3,
    },
    timeout=20,
)

print("STATUS:", response.status_code)
print("BODY:", response.text)