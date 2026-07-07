import math
import re
from dataclasses import dataclass
from pathlib import Path


KNOWLEDGE_DIR = Path("data/knowledge")

_ASCII_WORD_RE = re.compile(r"[a-zA-Z0-9_]+")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_HEADING_RE = re.compile(r"^#{1,3}\s+(.+)$", re.MULTILINE)


@dataclass(frozen=True)
class DocumentChunk:
    title: str
    source: str
    content: str
    terms: set[str]


class KnowledgeBase:
    def __init__(self, knowledge_dir: Path):
        self.knowledge_dir = knowledge_dir
        self._chunks: list[DocumentChunk] = []
        self._idf: dict[str, float] = {}
        self.reload()

    def reload(self) -> None:
        self._chunks = self._load_chunks()
        self._idf = self._build_idf(self._chunks)

    def search(self, query: str, top_k: int = 4) -> list[dict]:
        query_terms = tokenize(query)
        if not query_terms:
            return []

        scored: list[tuple[float, DocumentChunk]] = []

        for chunk in self._chunks:
            score = self._score(query_terms, chunk)
            if score > 0:
                scored.append((score, chunk))

        scored.sort(key=lambda item: item[0], reverse=True)

        return [
            {
                "title": chunk.title,
                "source": chunk.source,
                "score": round(score, 4),
                "content": chunk.content,
            }
            for score, chunk in scored[:top_k]
        ]

    def _score(self, query_terms: set[str], chunk: DocumentChunk) -> float:
        overlap = query_terms & chunk.terms

        if not overlap:
            return 0.0

        weighted_overlap = sum(self._idf.get(term, 1.0) for term in overlap)
        coverage = len(overlap) / max(len(query_terms), 1)
        length_penalty = 1.0 / math.sqrt(max(len(chunk.content) / 350, 1.0))

        return weighted_overlap * (0.7 + coverage) * length_penalty

    def _load_chunks(self) -> list[DocumentChunk]:
        if not self.knowledge_dir.exists():
            return []

        chunks: list[DocumentChunk] = []

        for path in sorted(self.knowledge_dir.glob("**/*.md")):
            text = path.read_text(encoding="utf-8").strip()
            if not text:
                continue

            chunks.extend(split_markdown(path, text))

        return chunks

    @staticmethod
    def _build_idf(chunks: list[DocumentChunk]) -> dict[str, float]:
        doc_freq: dict[str, int] = {}

        for chunk in chunks:
            for term in chunk.terms:
                doc_freq[term] = doc_freq.get(term, 0) + 1

        total = max(len(chunks), 1)

        return {
            term: math.log((total + 1) / (freq + 0.5)) + 1
            for term, freq in doc_freq.items()
        }


def split_markdown(path: Path, text: str) -> list[DocumentChunk]:
    matches = list(_HEADING_RE.finditer(text))

    if not matches:
        title = path.stem.replace("_", " ")
        return [make_chunk(title, str(path), text)]

    chunks: list[DocumentChunk] = []

    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)

        title = match.group(1).strip()
        body = text[start:end].strip()

        if body:
            chunks.append(make_chunk(title, str(path), body))

    return chunks


def make_chunk(title: str, source: str, content: str) -> DocumentChunk:
    return DocumentChunk(
        title=title,
        source=source,
        content=content,
        terms=tokenize(title + "\n" + content),
    )


def tokenize(text: str) -> set[str]:
    text = text.lower()

    terms = set(_ASCII_WORD_RE.findall(text))

    cjk_chars = _CJK_RE.findall(text)

    terms.update(cjk_chars)
    terms.update("".join(pair) for pair in zip(cjk_chars, cjk_chars[1:]))
    terms.update(
        "".join(cjk_chars[index : index + 3])
        for index in range(max(len(cjk_chars) - 2, 0))
    )

    return {term for term in terms if term.strip()}


knowledge_base = KnowledgeBase(KNOWLEDGE_DIR)


def search_knowledge(query: str, top_k: int = 4) -> list[dict]:
    return knowledge_base.search(query, top_k=top_k)