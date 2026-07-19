import json
import logging
import re

import httpx

from app.config import OPENAI_API_KEY, OPENAI_BASE_URL, OPENAI_MODEL


logger = logging.getLogger(__name__)

MODEL_OPTIONS = {
    "deepseek-chat": "deepseek-chat",
    "deepseek-reasoner": "deepseek-reasoner",
    "minimax": "minimax",
    "minimax-m2.7": "minimax-m2.7",
    "qwen": "qwen",
    "qwen3.6-27b": "qwen3.6-27b",
}
VISIT_TYPES = {"freshman_orientation", "scenic_tour", "parent_visit", "unknown"}


def resolve_model(model: str | None = None) -> str:
    if model in MODEL_OPTIONS:
        return MODEL_OPTIONS[model]
    return OPENAI_MODEL


def profile_role(profile=None) -> str:
    return getattr(profile, "role", "student") if profile else "student"


def role_instruction(profile=None) -> str:
    if profile_role(profile) == "parent":
        return (
            "当前用户是新生家长。回答要更关注家长视角，重点覆盖报到陪同、交通接送、"
            "材料和缴费确认、宿舍入住、安全和防诈骗、孩子适应大学等内容。"
            "不要替家长做过度承诺；涉及具体政策、时间、费用、电话时，必须提醒以学校/"
            "学院最新官方通知为准。"
        )

    return "当前用户是新生。回答要保持新生视角，侧重入学准备、校园生活和实际操作建议。"


def clean_context_text(text: str, max_chars: int = 520) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "..."


def generate_fallback_answer(question: str, contexts: list[dict], profile=None) -> str:
    best_content = clean_context_text(contexts[0]["content"]) if contexts else ""
    official_note = "具体安排以学校或学院最新通知为准。"
    best_source = contexts[0].get("source", "") if contexts else ""
    best_title = contexts[0].get("title", "") if contexts else ""

    if best_content and ("calendar_text" in best_source or "校历" in best_title):
        return f"{best_content}\n\n{official_note}"

    if profile_role(profile) == "parent":
        if best_content:
            return (
                "家长陪同报到时，可以重点关注材料核验、缴费状态、交通接送、宿舍入住、"
                "安全防诈骗和孩子适应情况。\n\n"
                f"{best_content}\n\n"
                f"{official_note}"
            )

        return (
            "家长陪同报到时，可以先帮孩子确认身份证、录取通知书、报到系统、缴费状态和学院通知；"
            "到校后协助完成宿舍入住和路线熟悉，同时尽量让孩子自己和学院、辅导员、宿管沟通。"
            "也要提醒孩子通过官方渠道缴费，警惕陌生链接、私人收款码和转账要求。\n\n"
            f"{official_note}"
        )

    if best_content:
        return (
            "报到当天可以先确认报到点、所需材料、宿舍入住、校园卡和学院通知；"
            "如果还有体检、班会、入学教育等安排，也建议当天一起核对清楚。\n\n"
            f"{best_content}\n\n"
            f"{official_note}"
        )

    return (
        "这个问题我暂时没有找到非常明确的资料。建议你补充校区、学院或具体场景，"
        "我再帮你判断；具体安排以学校或学院最新通知为准。"
    )


def build_prompt(
    question: str,
    contexts: list[dict],
    history: list = None,
    profile=None,
    tool_results: list[dict] = None,
) -> str:
    history = history or []
    tool_results = tool_results or []
    profile_text = profile.model_dump_json(exclude_none=True) if profile else "无"

    history_text = "\n".join(f"{item.role}: {item.content}" for item in history[-8:])
    context_text = "\n\n".join(
        f"资料 {index}: {item['title']}\n来源: {item['source']}\n内容: {item['content']}"
        for index, item in enumerate(contexts, start=1)
    )
    tool_text = "\n\n".join(
        f"工具: {item['name']}\n结果: {item['content']}"
        for item in tool_results
    )

    return f"""
你是上海交通大学新生小助手。请基于资料、工具结果和用户身份回答问题。

回答风格：
- 自然、亲切、实用，像一个熟悉校园事务的人在帮忙。
- 直接给建议，不要复述“你问的是……”。
- 不要说“我查到了一段资料”“我在知识库中找到”“资料显示”等暴露内部检索过程的话。
- 不要暴露工具调用、RAG、检索、prompt 等内部过程。
- 使用资料后，只需在末尾提醒“具体安排以学校或学院最新通知为准”。
- 如果资料不足，也请先给出稳妥的通用建议，再提醒以官方通知为准。

内容要求：
- 不要编造日期、地点、政策细节、费用、电话或联系人。
- 回答校园参观相关问题时，先判断用户参观目的：新生熟悉校园、游客景点打卡、家长陪同入学参观，按目的选择路线，不要默认所有参观需求都是游客路线。
- 如果工具结果里有 dining_tool 的食堂推荐，即使实时拥挤度或历史偏好暂未获取，也要基于工具给出的本地食堂知识库推荐明确回答。
- 回答食堂问题时，请区分依据来源：有实时拥挤度就说明实时依据；没有实时拥挤度时说明“先按本地食堂知识库和已记录偏好推荐”。
- 如果工具结果里有 parent_tool，请结合家长陪同报到清单给出家长视角建议。

用户身份说明：
{role_instruction(profile)}

用户资料：
{profile_text}

历史对话：
{history_text or "无"}

最新用户问题：
{question}

参考资料：
{context_text or "没有检索到相关资料"}

工具结果：
{tool_text or "无"}
"""


def generate_answer(
    question: str,
    contexts: list[dict],
    history: list = None,
    profile=None,
    tool_results: list[dict] = None,
    model: str | None = None,
) -> tuple[str, bool]:
    if not OPENAI_API_KEY:
        logger.warning("没有读取到 OPENAI_API_KEY，使用本地 fallback 回答")
        return generate_fallback_answer(question, contexts, profile=profile), False

    selected_model = resolve_model(model)
    prompt = build_prompt(
        question=question,
        contexts=contexts,
        history=history or [],
        profile=profile,
        tool_results=tool_results,
    )

    try:
        response = httpx.post(
            f"{OPENAI_BASE_URL.rstrip('/')}/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": selected_model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.3,
            },
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"], True
    except Exception:
        logger.exception("LLM 调用失败，已使用 fallback 回答: model=%s", selected_model)
        return generate_fallback_answer(question, contexts, profile=profile), False


def fallback_visit_type(question: str, profile=None) -> str:
    if profile_role(profile) == "parent":
        if any(word in question for word in ["拍照", "打卡", "景点", "风景", "游客", "朋友"]):
            return "scenic_tour"
        return "parent_visit"
    if any(word in question for word in ["家长", "送孩子", "陪同", "孩子", "接送"]):
        return "parent_visit"
    if any(word in question for word in ["拍照", "打卡", "景点", "风景", "游客", "朋友", "好看"]):
        return "scenic_tour"
    if any(word in question for word in ["第一次", "新生", "熟悉", "了解", "开学", "入校", "校园"]):
        return "freshman_orientation"
    return "unknown"


def classify_visit_type(
    question: str,
    history: list = None,
    profile=None,
    model: str | None = None,
) -> str:
    if not OPENAI_API_KEY:
        return fallback_visit_type(question, profile=profile)

    selected_model = resolve_model(model)
    history = history or []
    history_text = "\n".join(f"{item.role}: {item.content}" for item in history[-6:])
    profile_text = profile.model_dump_json(exclude_none=True) if profile else "无"

    prompt = f"""
你是上海交通大学新生助手的参观目的分类器。请根据用户问题、历史对话和用户身份，判断校园参观需求的 visit_type。

visit_type 只能是：
- freshman_orientation：新生第一次入校，想熟悉教学楼、图书馆、食堂、宿舍、学生服务等日常学习生活地点。
- scenic_tour：游客、朋友来访、拍照打卡、校园景点、风景和校史建筑游览。
- parent_visit：家长送孩子报到或陪同入学，想了解学院、宿舍生活区、食堂、校园环境、安全支持等。
- unknown：参观目的不明确。

判断规则：
- 不要默认所有“参观路线”都是游客路线。
- “第一次来交大，想熟悉/了解校园”优先 freshman_orientation。
- “拍照、打卡、景点、风景、游客、带朋友逛”优先 scenic_tour。
- “家长、送孩子、陪同报到、看看孩子未来环境”优先 parent_visit。
- 若 profile.role 是 parent 且问题没有明显游客拍照意图，优先 parent_visit。

只输出 JSON：
{{"visit_type":"freshman_orientation|scenic_tour|parent_visit|unknown"}}

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
                "model": selected_model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0,
            },
            timeout=8,
        )
        response.raise_for_status()
        data = response.json()
        parsed = extract_json_object(data["choices"][0]["message"]["content"])
    except Exception:
        logger.exception("参观目的分类 LLM 调用失败，已使用本地规则: model=%s", selected_model)
        return fallback_visit_type(question, profile=profile)

    visit_type = parsed.get("visit_type") if isinstance(parsed, dict) else None
    if visit_type in VISIT_TYPES:
        return visit_type

    return fallback_visit_type(question, profile=profile)


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


def plan_tool_use(
    question: str,
    history: list = None,
    profile=None,
    model: str | None = None,
) -> dict | None:
    if not OPENAI_API_KEY:
        return None

    selected_model = resolve_model(model)
    history = history or []
    history_text = "\n".join(f"{item.role}: {item.content}" for item in history[-8:])
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
- “包图怎么走”“怎么去包图”“去图书馆”“从宿舍到电院”“开车送孩子去东一宿舍怎么走”都应使用 walking_route。
- “包图在哪”“电院位置”“附近的校门”应使用 place_search。
- 地点可以是简称、别名、口语说法；不要因为本地地点库可能没有就放弃工具。
- 尽量把简称改写为上海交通大学常见规范名称，例如“包图”应规范化为“包玉刚图书馆”。
- 如果用户没说校区但语境是上海交通大学本科新生，优先按“闵行校区”理解。
- “去哪吃”“推荐食堂”“哪个食堂不挤”“三餐人多吗”应使用 dining_recommend。
- “我去三餐吃了”“今天吃了哈乐”“我常去二餐”应使用 dining_record。
- 如果不是地点、导航或食堂问题，action 用 "none"，tool 用 null。

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
                "model": selected_model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0,
            },
            timeout=8,
        )
        response.raise_for_status()
        data = response.json()
        content = data["choices"][0]["message"]["content"]
    except Exception:
        logger.exception("工具规划 LLM 调用失败，已跳过工具规划: model=%s", selected_model)
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
