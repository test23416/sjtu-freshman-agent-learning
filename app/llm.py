import httpx
import json

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


def build_prompt(question: str, contexts: list[dict], history: list = None, profile = None,tool_results: list[dict] = None) -> str:
    history = history or []
    tool_results = tool_results or []
    if profile:
        profile_text = profile.model_dump_json(exclude_none=True)
    else:
        profile_text = "无"

    history_text = "\n".join(
        f"{item.role}:{item.content}"
        for item in history[-8:]
    )

    context_text = "\n\n".join(
        f"资料 {index}:{item['title']}\n来源:{item['source']}\n内容:{item['content']}"
        for index, item in enumerate(contexts, start=1)
    )

    tool_text = "\n\n".join(
    f"工具:{item['name']}\n结果:{item['content']}"
    for item in tool_results
    )

    return f"""
你是上海交通大学新生小助手。
请基于下面的资料回答用户问题。
如果资料不足，请明确说明需要以学校或学院最新官方通知为准。
不要编造日期、地点、政策细节。
如果工具结果里有 dining_tool 的食堂推荐，即使实时拥挤度或历史偏好暂未获取，也要基于工具给出的本地食堂知识库推荐明确回答；不要只说“无法推荐”。
回答食堂问题时，区分依据来源：有实时拥挤度就说明实时依据；没有实时拥挤度时说明“先按本地食堂知识库和已记录偏好推荐”。

历史对话：
{history_text or "无"}

最新用户问题：
{question}

参考资料：
{context_text or "没有检索到相关资料"}

工具结果：
{tool_text or "无"}
"""


def generate_answer(question: str, contexts: list[dict], history: list = None, profile = None,tool_results: list[dict] = None) -> tuple[str, bool]:
    if not OPENAI_API_KEY:
        print("没有读取到 OPENAI_API_KE,使用本地回答")
        return generate_fallback_answer(question, contexts), False

    history = history or []
    prompt = build_prompt(
    question=question,
    contexts=contexts,
    history=history,
    profile=profile,
    tool_results=tool_results,
    )

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


def extract_json_object(text: str) -> dict | None:
    text = text.strip()

    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:].strip()

    start = text.find("{")
    end = text.rfind("}")

    if start == -1 or end == -1 or end < start:
        return None

    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None


def plan_tool_use(question: str, history: list = None, profile = None) -> dict | None:
    if not OPENAI_API_KEY:
        return None

    history = history or []
    history_text = "\n".join(
        f"{item.role}:{item.content}"
        for item in history[-8:]
    )
    profile_text = profile.model_dump_json(exclude_none=True) if profile else "无"

    prompt = f"""
你是上海交通大学新生助手的工具规划器。你的任务不是回答用户，而是判断是否需要调用校园工具。

可用工具：
1. campus_place_tool.place_search(place)
   用于查询地点、位置、地图链接。
2. campus_place_tool.walking_route(origin, destination)
   用于步行导航、路线规划。origin 可以为 null；当用户没说起点时，系统会默认使用当前位置或上下文补全。
3. dining_tool.dining_recommend(campus, canteen)
   用于食堂推荐、实时拥挤度、去哪吃饭。canteen 可以为 null。
4. dining_tool.dining_record(canteen)
   用于记录用户已经去某个食堂吃了，作为历史偏好。

请只输出一个 JSON 对象，不要输出解释文字：
{{
  "tool": "campus_place_tool" 或 "dining_tool" 或 null,
  "action": "place_search" 或 "walking_route" 或 "dining_recommend" 或 "dining_record" 或 "none",
  "place": "地点名或 null",
  "normalized_place": "规范化地点名或 null",
  "origin": "起点地点名或 null",
  "normalized_origin": "规范化起点或 null",
  "destination": "终点地点名或 null",
  "normalized_destination": "规范化终点或 null",
  "campus": "校区名或 null",
  "reason": "一句话说明"
}}

判断规则：
- “包图怎么走”“怎么去包图”“去图书馆”“从宿舍到电院”都应使用 walking_route。
- “包图在哪”“电院位置”“附近的校门”应使用 place_search。
- 地点可以是简称、别名、口语说法；不要因为本地地点库可能没有就放弃工具。
- 尽量把简称改写为上海交通大学常见规范名称，例如“包图”应规范化为“包玉刚图书馆”。
- 如果用户没说校区但语境是上海交通大学本科新生，优先按“闵行校区”理解。
- “去哪吃”“推荐食堂”“哪个食堂不挤”“三餐人多吗”应使用 dining_recommend。
- “我去三餐吃了”“今天吃了哈乐”“我常去二餐”应使用 dining_record。
- 如果不是地点或导航问题，action 用 "none"，tool 用 null。

历史对话：
{history_text or "无"}

用户资料：
{profile_text}

最新用户问题：
{question}
"""

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
                "temperature": 0,
            },
            timeout=20,
        )
        response.raise_for_status()
        data = response.json()
        content = data["choices"][0]["message"]["content"]
    except Exception as error:
        print("工具规划调用大模型失败:", repr(error))
        return None

    plan = extract_json_object(content)

    if not isinstance(plan, dict):
        return None

    if plan.get("tool") not in {"campus_place_tool", "dining_tool"}:
        return None

    if plan.get("action") not in {
        "place_search",
        "walking_route",
        "dining_recommend",
        "dining_record",
    }:
        return None

    return plan
