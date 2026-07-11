import argparse
import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PageText:
    page_number: int
    text: str


def main() -> None:
    args = parse_args()

    if args.all:
        import_all_pdfs(
            raw_dir=args.raw_dir,
            knowledge_dir=args.knowledge_dir,
            title=args.title,
            chunk_pages=args.chunk_pages,
            min_chars=args.min_chars,
            overwrite=args.overwrite,
        )
        return

    if args.pdf is None:
        raise SystemExit("PDF path is required unless you use --all.")

    import_one_pdf(
        pdf_path=args.pdf,
        output_path=args.output,
        title=args.title,
        chunk_pages=args.chunk_pages,
        min_chars=args.min_chars,
        overwrite=args.overwrite,
    )


def import_one_pdf(
    pdf_path: Path,
    output_path: Path,
    title: str,
    chunk_pages: int,
    min_chars: int,
    overwrite: bool,
) -> None:
    pdf_path = pdf_path.resolve()
    output_path = output_path.resolve()

    if not pdf_path.exists():
        raise SystemExit(f"PDF not found: {pdf_path}")

    if output_path.exists() and not overwrite:
        raise SystemExit(
            f"Output already exists: {output_path}. Use --overwrite to replace it."
        )

    pages = extract_pdf_pages(pdf_path)
    cleaned_pages = clean_pages(pages)
    markdown = build_markdown(
        title=title,
        pdf_name=pdf_path.name,
        pages=cleaned_pages,
        chunk_pages=chunk_pages,
        min_chars=min_chars,
    )

    if markdown.count("# ") < 1:
        raise SystemExit(
            f"No usable text was extracted from {pdf_path}. "
            "If this is a scanned PDF, run OCR first, then import the OCR PDF."
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(markdown, encoding="utf-8")
    print(f"Imported {len(cleaned_pages)} pages into {output_path}")


def import_all_pdfs(
    raw_dir: Path,
    knowledge_dir: Path,
    title: str,
    chunk_pages: int,
    min_chars: int,
    overwrite: bool,
) -> None:
    raw_dir = raw_dir.resolve()
    knowledge_dir = knowledge_dir.resolve()

    if not raw_dir.exists():
        raise SystemExit(f"Raw directory not found: {raw_dir}")

    pdf_files = sorted(raw_dir.glob("*.pdf"))

    if not pdf_files:
        raise SystemExit(f"No PDF files found in {raw_dir}")

    success_count = 0
    failed_count = 0
    skipped_count = 0

    for pdf_path in pdf_files:
        output_path = knowledge_dir / f"{pdf_path.stem}.md"

        try:
            import_one_pdf(
                pdf_path=pdf_path,
                output_path=output_path,
                title=title,
                chunk_pages=chunk_pages,
                min_chars=min_chars,
                overwrite=overwrite,
            )
            success_count += 1
        except SystemExit as exc:
            message = str(exc)
            if "Output already exists" in message:
                skipped_count += 1
                print(f"[SKIP] {pdf_path.name}: {message}")
            else:
                failed_count += 1
                print(f"[FAILED] {pdf_path.name}: {message}")
        except Exception as exc:
            failed_count += 1
            print(f"[ERROR] {pdf_path.name}: {exc}")

    print()
    print(
        f"Batch import finished. "
        f"Success: {success_count}, Skipped: {skipped_count}, Failed: {failed_count}"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Import freshman handbook PDF files into the Markdown knowledge base."
    )

    parser.add_argument(
        "pdf",
        type=Path,
        nargs="?",
        help="Path to one PDF handbook. Not required when using --all.",
    )

    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/knowledge/imported_handbook.md"),
        help="Output Markdown file for single PDF mode. Default: data/knowledge/imported_handbook.md",
    )

    parser.add_argument(
        "--title",
        default="新生手册",
        help="Knowledge base title prefix.",
    )

    parser.add_argument(
        "--chunk-pages",
        type=int,
        default=3,
        help="How many PDF pages to group into one Markdown knowledge chunk.",
    )

    parser.add_argument(
        "--min-chars",
        type=int,
        default=80,
        help="Skip page groups with fewer extracted characters than this threshold.",
    )

    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite output file if it exists.",
    )

    parser.add_argument(
        "--all",
        action="store_true",
        help="Convert all PDF files under raw directory.",
    )

    parser.add_argument(
        "--raw-dir",
        type=Path,
        default=Path("data/raw"),
        help="Directory containing raw PDF files. Default: data/raw",
    )

    parser.add_argument(
        "--knowledge-dir",
        type=Path,
        default=Path("data/knowledge"),
        help="Directory to write Markdown files in batch mode. Default: data/knowledge",
    )

    return parser.parse_args()


def extract_pdf_pages(pdf_path: Path) -> list[PageText]:
    try:
        from pypdf import PdfReader
    except ModuleNotFoundError as exc:
        raise SystemExit(
            "Missing dependency: pypdf. Install it with `pip install pypdf`, "
            'or run `pip install -e ".[pdf]"` from the project root.'
        ) from exc

    reader = PdfReader(str(pdf_path))
    pages: list[PageText] = []

    for index, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        pages.append(PageText(page_number=index, text=text))

    return pages


def clean_pages(pages: list[PageText]) -> list[PageText]:
    repeated_lines = find_repeated_lines(pages)
    cleaned: list[PageText] = []

    for page in pages:
        lines = []

        for line in normalize_lines(page.text):
            if line in repeated_lines:
                continue
            if is_noise_line(line):
                continue
            lines.append(line)

        page_text = "\n".join(lines).strip()

        if page_text:
            cleaned.append(PageText(page_number=page.page_number, text=page_text))

    return cleaned


def find_repeated_lines(pages: list[PageText]) -> set[str]:
    counts: Counter[str] = Counter()

    for page in pages:
        seen_on_page = set(normalize_lines(page.text))
        counts.update(line for line in seen_on_page if 2 <= len(line) <= 80)

    threshold = max(3, int(len(pages) * 0.3))
    return {line for line, count in counts.items() if count >= threshold}


def normalize_lines(text: str) -> list[str]:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\u00a0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    lines = [line.strip() for line in text.splitlines()]
    return [line for line in lines if line]


def is_noise_line(line: str) -> bool:
    if re.fullmatch(r"\d+", line):
        return True

    if re.fullmatch(r"[-_—–·•\s]+", line):
        return True

    return False


def build_markdown(
    title: str,
    pdf_name: str,
    pages: list[PageText],
    chunk_pages: int,
    min_chars: int,
) -> str:
    if chunk_pages < 1:
        raise ValueError("chunk_pages must be >= 1")

    chunks: list[str] = [
        f"<!-- Imported from {pdf_name}. Review important dates and policies against official notices. -->"
    ]

    for group in group_pages(pages, chunk_pages):
        body = "\n\n".join(page.text for page in group).strip()

        if len(body) < min_chars:
            continue

        start_page = group[0].page_number
        end_page = group[-1].page_number
        heading = infer_heading(body) or f"{title} 第 {start_page}-{end_page} 页"

        chunks.append(
            f"# {heading}\n\n"
            f"来源：{pdf_name}，第 {start_page}-{end_page} 页。\n\n"
            f"{body}"
        )

    return "\n\n".join(chunks).strip() + "\n"


def group_pages(pages: list[PageText], size: int) -> list[list[PageText]]:
    return [pages[index : index + size] for index in range(0, len(pages), size)]


def infer_heading(text: str) -> str | None:
    for line in normalize_lines(text)[:6]:
        candidate = line.strip(" #")

        if 4 <= len(candidate) <= 36 and not candidate.endswith(("。", "，", "；", "：")):
            return candidate

    return None


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)