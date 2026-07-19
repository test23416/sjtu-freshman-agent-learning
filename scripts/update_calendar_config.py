from __future__ import annotations

import argparse
import json
import re
from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx


DEFAULT_OUTPUT = Path("data/official/calendar.json")
DEFAULT_RAW_DIR = Path("data/raw")
DEFAULT_TITLE = "上海交通大学校历"
DEFAULT_DESCRIPTION = "校历信息来自上海交通大学官网，请以官网最新版本为准。"
DEFAULT_LIST_URL = "https://jwc.sjtu.edu.cn/jxxl/lnxl.htm"


class LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[dict[str, str]] = []
        self._href: str | None = None
        self._text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return

        attrs_dict = {key.lower(): value or "" for key, value in attrs}
        self._href = attrs_dict.get("href")
        self._text_parts = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or self._href is None:
            return

        text = " ".join("".join(self._text_parts).split())
        if text and self._href:
            self.links.append({"href": self._href, "text": text})

        self._href = None
        self._text_parts = []


class ImageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.images: list[dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "img":
            return

        self.images.append({key.lower(): value or "" for key, value in attrs})


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Update offline SJTU calendar config.")
    parser.add_argument("--auto", action="store_true", help="Fetch JWC calendar pages and auto-detect the calendar asset.")
    parser.add_argument("--list-url", default=DEFAULT_LIST_URL, help="JWC calendar list page used by --auto.")
    parser.add_argument("--pdf-url", help="Official calendar PDF/image URL. Required unless --auto is used.")
    parser.add_argument("--school-year", help='Academic year, for example "2026-2027". Defaults to current academic year in --auto mode.')
    parser.add_argument("--semester", help='Semester name, for example "秋季学期". Auto mode can infer it from the page title.')
    parser.add_argument("--source-url", help="Official source page URL. Required unless --auto is used.")
    parser.add_argument("--updated-at", default=date.today().isoformat(), help="Update date in YYYY-MM-DD.")
    parser.add_argument("--title", default=DEFAULT_TITLE)
    parser.add_argument("--description", default=DEFAULT_DESCRIPTION)
    parser.add_argument("--local-file", default=None, help="Optional local file path under data/raw.")
    parser.add_argument("--download", action="store_true", help="Download the detected/provided calendar asset into data/raw.")
    parser.add_argument("--calendar-year", type=int, help='Local file year, for example 2026 creates data/raw/2026_calendar.pdf. Defaults to current year.')
    parser.add_argument("--raw-dir", default=str(DEFAULT_RAW_DIR), help="Directory for downloaded calendar files.")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help=argparse.SUPPRESS)
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing config.")
    return parser.parse_args()


def current_academic_year() -> str:
    today = date.today()
    start = today.year if today.month >= 7 else today.year - 1
    return f"{start}-{start + 1}"


def current_calendar_year() -> int:
    return date.today().year


def normalize_url(base_url: str, href: str) -> str:
    if href.startswith("info/") and "jwc.sjtu.edu.cn" in base_url:
        return urljoin("https://jwc.sjtu.edu.cn/", href)

    return urljoin(base_url, href)


def normalize_calendar_asset_url(base_url: str, href: str) -> str:
    url = normalize_url(base_url, href)
    parsed = urlsplit(url)

    # The JWC article page sometimes adds cache query strings to image assets.
    if "/__local/" in parsed.path and parsed.path.lower().endswith((".png", ".jpg", ".jpeg")):
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))

    return url


def parse_calendar_list(html: str, base_url: str) -> list[dict[str, str]]:
    parser = LinkParser()
    parser.feed(html)

    entries = []
    for link in parser.links:
        title = link["text"]
        if "校历" not in title:
            continue
        if not re.search(r"20\d{2}", title):
            continue

        entries.append(
            {
                "title": title,
                "url": normalize_url(base_url, link["href"]),
            }
        )

    return entries


def score_calendar_title(title: str, school_year: str) -> int:
    compact_title = re.sub(r"\s+", "", title)
    start, end = school_year.split("-", 1)
    score = 0

    if "校历" in compact_title:
        score += 50
    if school_year in compact_title:
        score += 150
    if f"{start}-{end}" in compact_title or f"{start}_{end}" in compact_title:
        score += 150
    if start in compact_title:
        score += 45
    if end in compact_title:
        score += 45
    if "上海交通大学" in compact_title:
        score += 10

    other_ranges = re.findall(r"(20\d{2})\s*[-—–至到]\s*(20\d{2})", compact_title)
    for other_start, other_end in other_ranges:
        if f"{other_start}-{other_end}" != school_year:
            score -= 200

    return score


def choose_calendar_entry(entries: list[dict[str, str]], school_year: str) -> dict[str, str]:
    scored = [(score_calendar_title(entry["title"], school_year), entry) for entry in entries]
    scored = [(score, entry) for score, entry in scored if score > 0]

    if not scored:
        raise SystemExit(f"未在教务处校历列表中找到 {school_year} 学年的校历链接。")

    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[0][1]


def safe_int(value: str | None) -> int:
    if not value:
        return 0

    match = re.search(r"\d+", value)
    return int(match.group()) if match else 0


def image_score(image: dict[str, str]) -> int:
    src_candidates = [image.get("src", ""), image.get("orisrc", ""), image.get("vurl", "")]
    src_text = " ".join(src_candidates).lower()
    class_name = image.get("class", "")
    width = safe_int(image.get("width") or image.get("vwidth"))
    height = safe_int(image.get("height") or image.get("vheight"))
    score = width + height

    if "__local" in src_text:
        score += 3000
    if "img_vsb_content" in class_name:
        score += 1200
    if "_vsl" in src_text:
        score -= 200
    if "logo" in src_text or "banner" in src_text:
        score -= 3000

    return score


def extract_calendar_asset_url(detail_html: str, detail_url: str) -> str:
    parser = ImageParser()
    parser.feed(detail_html)

    candidates: list[tuple[int, str]] = []
    for image in parser.images:
        # Prefer the real src/orisrc path. vurl can be a JWC virtual path and is less stable.
        for attr in ("src", "orisrc", "vurl"):
            href = image.get(attr)
            if not href:
                continue
            if not re.search(r"\.(png|jpe?g|pdf)(\?|$)", href, re.IGNORECASE):
                continue
            candidates.append((image_score(image), normalize_calendar_asset_url(detail_url, href)))

    pdf_links = re.findall(r"href=[\"']([^\"']+\.pdf(?:\?[^\"']*)?)[\"']", detail_html, flags=re.IGNORECASE)
    for href in pdf_links:
        candidates.append((5000, normalize_calendar_asset_url(detail_url, href)))

    if not candidates:
        raise SystemExit("已找到校历详情页，但没有提取到 PDF 或校历图片地址。")

    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def infer_semester(title: str) -> str:
    if "秋" in title:
        return "秋季学期"
    if "春" in title:
        return "春季学期"
    if "夏" in title:
        return "夏季学期"
    return ""


def fetch_auto_calendar(list_url: str, school_year: str) -> dict[str, str]:
    headers = {"User-Agent": "Mozilla/5.0 SJTU-Freshman-Agent-Maintenance/1.0"}

    list_response = httpx.get(list_url, headers=headers, timeout=12)
    list_response.raise_for_status()
    entries = parse_calendar_list(list_response.text, list_url)
    entry = choose_calendar_entry(entries, school_year)

    detail_response = httpx.get(entry["url"], headers=headers, timeout=12)
    detail_response.raise_for_status()
    asset_url = extract_calendar_asset_url(detail_response.text, entry["url"])

    return {
        "title": entry["title"],
        "pdf_url": asset_url,
        "source_url": entry["url"],
        "semester": infer_semester(entry["title"]),
    }


def suffix_from_response(url: str, content_type: str) -> str:
    path_suffix = Path(urlsplit(url).path).suffix.lower()
    if path_suffix in {".pdf", ".png", ".jpg", ".jpeg"}:
        return path_suffix

    content_type = content_type.lower()
    if "pdf" in content_type:
        return ".pdf"
    if "png" in content_type:
        return ".png"
    if "jpeg" in content_type or "jpg" in content_type:
        return ".jpg"

    return ".bin"


def convert_image_to_pdf(image_path: Path, pdf_path: Path) -> None:
    try:
        from PIL import Image
    except ImportError as error:
        raise SystemExit(
            "官网返回的是图片格式，转换为 PDF 需要 Pillow。请先安装：pip install pillow"
        ) from error

    with Image.open(image_path) as image:
        if image.mode in ("RGBA", "LA", "P"):
            image = image.convert("RGB")
        image.save(pdf_path, "PDF", resolution=100.0)


def download_calendar_asset(
    asset_url: str,
    calendar_year: int,
    raw_dir: Path,
    overwrite: bool = False,
) -> Path:
    raw_dir.mkdir(parents=True, exist_ok=True)
    headers = {"User-Agent": "Mozilla/5.0 SJTU-Freshman-Agent-Maintenance/1.0"}

    response = httpx.get(asset_url, headers=headers, timeout=30, follow_redirects=True)
    response.raise_for_status()

    suffix = suffix_from_response(asset_url, response.headers.get("content-type", ""))
    pdf_path = raw_dir / f"{calendar_year}_calendar.pdf"

    if pdf_path.exists() and not overwrite:
        raise SystemExit(f"{pdf_path} already exists. Use --overwrite to replace it.")

    if suffix == ".pdf":
        pdf_path.write_bytes(response.content)
        return pdf_path

    if suffix in {".png", ".jpg", ".jpeg"}:
        image_path = raw_dir / f"{calendar_year}_calendar_source{suffix}"
        if image_path.exists() and not overwrite:
            raise SystemExit(f"{image_path} already exists. Use --overwrite to replace it.")

        image_path.write_bytes(response.content)
        convert_image_to_pdf(image_path, pdf_path)
        return pdf_path

    fallback_path = raw_dir / f"{calendar_year}_calendar_source{suffix}"
    fallback_path.write_bytes(response.content)
    raise SystemExit(
        f"已下载资源到 {fallback_path}，但无法转换为 PDF。请手动检查官网资源格式。"
    )


def build_config(args: argparse.Namespace) -> dict[str, Any]:
    school_year = args.school_year or current_academic_year()
    title = args.title
    semester = args.semester or ""
    pdf_url = args.pdf_url
    source_url = args.source_url

    if args.auto:
        detected = fetch_auto_calendar(args.list_url, school_year)
        title = args.title if args.title != DEFAULT_TITLE else detected["title"]
        semester = args.semester or detected["semester"]
        pdf_url = args.pdf_url or detected["pdf_url"]
        source_url = args.source_url or detected["source_url"]

    if not pdf_url:
        raise SystemExit("--pdf-url is required unless --auto can detect one.")
    if not source_url:
        raise SystemExit("--source-url is required unless --auto can detect one.")

    local_file = args.local_file
    if args.download:
        calendar_year = args.calendar_year or current_calendar_year()
        downloaded = download_calendar_asset(
            asset_url=pdf_url,
            calendar_year=calendar_year,
            raw_dir=Path(args.raw_dir),
            overwrite=args.overwrite,
        )
        local_file = downloaded.as_posix()

    return {
        "title": title,
        "school_year": school_year,
        "semester": semester,
        "pdf_url": pdf_url,
        "local_file": local_file,
        "source_url": source_url,
        "updated_at": args.updated_at,
        "description": args.description,
    }


def main() -> None:
    args = parse_args()
    output = Path(args.output)

    if output.exists() and not args.overwrite:
        raise SystemExit(f"{output} already exists. Use --overwrite to replace it.")

    output.parent.mkdir(parents=True, exist_ok=True)
    data = build_config(args)

    with output.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)
        file.write("\n")

    print(f"Updated {output}")
    print(f"- title: {data['title']}")
    print(f"- school_year: {data['school_year']}")
    print(f"- semester: {data['semester'] or '未指定'}")
    print(f"- pdf_url: {data['pdf_url']}")
    print(f"- local_file: {data['local_file'] or '未指定'}")
    print(f"- source_url: {data['source_url']}")
    print(f"- updated_at: {data['updated_at']}")


if __name__ == "__main__":
    main()
