import httpx

from app.config import OPENAI_API_KEY, OPENAI_BASE_URL, OPENAI_MODEL


def generate_fallback_answer(question: str, contexts: list[dict]) -> str:
    if not contexts:
        return "我还没有在知识库里找到相关信息。"

    best = contexts[0]

    return (
        f"你问的是：{question}\n\n"
        f"我在知识库中找到了相关信息：\n"
        f"{best['content']}\n\n"
        f"建议你以学校或学院最新官方通知为准。"
    )


def build_prompt(question: str, contexts: list[dict], history: list = None, profile = None) -> str:
    history = history or []

    if profile:
        profile_text = profile.model_dump_json(exclude_none=True)
    else:
        profile_text = "无"
        
    history_text = "\n".join(
        f"{item.role}:{item.content}"
        for item in history[-8:]
    )

    context_text = "\n\n".join(
        f"资料 {index}：{item['title']}\n来源：{item['source']}\n内容：{item['content']}"
        for index, item in enumerate(contexts, start=1)
    )

    return f"""
你是上海交通大学新生小助手。
请基于下面的资料回答用户问题。
如果资料不足，请明确说明需要以学校或学院最新官方通知为准。
不要编造日期、地点、政策细节。

历史对话：
{history_text or "无"}

最新用户问题：
{question}

参考资料：
{context_text or "没有检索到相关资料"}
"""


def generate_answer(question: str, contexts: list[dict], history: list = None, profile = None) -> tuple[str, bool]:
    if not OPENAI_API_KEY:
        print("没有读取到 OPENAI_API_KE,使用本地回答")
        return generate_fallback_answer(question, contexts), False

    history = history or []
    prompt = build_prompt(question, contexts,history,profile)

    print("准备调用大模型")
    print("BASE_URL:", OPENAI_BASE_URL)
    print("MODEL:", OPENAI_MODEL)

    try:
        response = httpx.post(
            f"{OPENAI_BASE_URL.rstrip('/')}/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": OPENAI_MODEL,
                "messages": [
                    {
                        "role": "user",
                        "content": prompt,
                    }
                ],
                "temperature": 0.3,
            },
            timeout=60,
        )

        print("大模型返回状态码:", response.status_code)
        print("大模型返回内容:", response.text)

        response.raise_for_status()
        data = response.json()

        answer = data["choices"][0]["message"]["content"]
        return answer, True

    except Exception as error:
        print("调用大模型失败:", repr(error))
        return (
            "我尝试调用大模型，但接口暂时没有成功返回。"
            "请检查 OPENAI_BASE_URL、OPENAI_MODEL、API Key 或网络连接。",
            False,
        )