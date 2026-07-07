from pathlib import Path


KNOWLEDGE_DIR = Path("data/knowledge")

STOP_WORDS = {
    "我",
    "你",
    "他",
    "她",
    "它",
    "的",
    "了",
    "是",
    "在",
    "和",
    "与",
    "要",
    "想",
    "请",
    "一下",
    "什么",
    "怎么",
    "如何",
    "哪些",
    "有没有",
}

DOMAIN_KEYWORDS = [
    "新生",
    "入学",
    "报到",
    "材料",
    "录取通知书",
    "身份证",
    "照片",
    "党团组织关系",
    "户口迁移",
    "资助",
    "贷款",
    "校园卡",
    "食堂",
    "宿舍",
    "校园网",
    "VPN",
    "选课",
    "课程",
]


def load_documents() -> list[dict]:
    documents = []

    for path in KNOWLEDGE_DIR.glob("*.md"):
        text = path.read_text(encoding="utf-8")

        sections = text.split("# ")
        for section in sections:
            section = section.strip()
            if not section:
                continue

            lines = section.splitlines()
            title = lines[0].strip()
            content = "\n".join(lines[1:]).strip()

            documents.append(
                {
                    "title": title,
                    "source": str(path),
                    "content": content,
                }
            )

    return documents


def extract_keywords(query: str) -> list[str]:
    keywords = []

    for keyword in DOMAIN_KEYWORDS:
        if keyword.lower() in query.lower():
            keywords.append(keyword)

    if keywords:
        return keywords

    for char in query:
        char = char.strip()
        if not char:
            continue
        if char in STOP_WORDS:
            continue
        keywords.append(char)

    return keywords


def search_knowledge(query: str) -> list[dict]:
    documents = load_documents()
    keywords = extract_keywords(query)
    results = []

    for doc in documents:
        score = 0
        searchable_text = doc["title"] + "\n" + doc["content"]

        for keyword in keywords:
            if keyword in doc["title"]:
                score += 3
            elif keyword in searchable_text:
                score += 1

        if score > 0:
            results.append(
                {
                    "title": doc["title"],
                    "source": doc["source"],
                    "content": doc["content"],
                    "score": score,
                }
            )

    results.sort(key=lambda item: item["score"], reverse=True)
    return results[:3]