def detect_tools(question: str) -> list[str]:
    tools = []

    if any(word in question for word in ["通知", "公告", "最新", "今天", "现在"]):
        tools.append("latest_notices")

    if any(word in question for word in ["校历", "放假", "开学", "考试周"]):
        tools.append("school_calendar")

    return tools


def get_latest_notices() -> str:
    return "这里以后接入学校或学院官网通知。目前暂未接入实时通知源。"


def get_school_calendar() -> str:
    return "这里以后接入校历。目前请以学校官方校历为准。"


def run_official_tools(question: str) -> list[dict]:
    tool_names = detect_tools(question)
    results = []

    for name in tool_names:
        if name == "latest_notices":
            results.append({
                "name": "latest_notices",
                "content": get_latest_notices(),
            })

        if name == "school_calendar":
            results.append({
                "name": "school_calendar",
                "content": get_school_calendar(),
            })

    return results