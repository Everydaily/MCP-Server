#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""NST 규정집 MCP 서버 — 국가과학기술연구회 규정집 게시판 연동

URL 파라미터 구조:
  searchRegltn       : 기관 코드 (REGLTN01=NST, REGLTN02=KICA, REGLTN03=BRIC)
  searchUpperRegltnNo: 편 번호 (1, 3, 7, 11, ... — select 옵션 value)
"""

import asyncio
import io
import re
import sys
from typing import Any
from urllib.parse import urljoin, quote

import httpx
from bs4 import BeautifulSoup
from pypdf import PdfReader
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types

# Python 3.12 미만 Windows에서 ProactorEventLoop 대신 SelectorEventLoop 사용 (httpx 호환)
if sys.platform == "win32" and sys.version_info < (3, 12):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

BASE_URL = "https://www.nst.re.kr/rulebook/"

ORGANIZATIONS: dict[str, dict[str, str]] = {
    "NST":  {"name": "국가과학기술연구회", "regltn": "REGLTN01"},
    "KICA": {"name": "산업기술연구회",     "regltn": "REGLTN02"},
    "BRIC": {"name": "기초기술연구회",     "regltn": "REGLTN03"},
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
    "Referer": "https://www.nst.re.kr/",
}

# search_regulations 병렬 요청 제한 (NST 서버 부하 방지)
_SEMAPHORE = asyncio.Semaphore(5)


async def fetch_page(
    url: str,
    params: dict | None = None,
    client: httpx.AsyncClient | None = None,
) -> BeautifulSoup:
    """URL을 가져와 BeautifulSoup 객체 반환.

    client를 전달하면 기존 세션을 재사용하고, 없으면 새로 생성한다.
    html.parser를 사용해 lxml 추가 설치 없이 동작한다.
    """
    async def _get(c: httpx.AsyncClient) -> BeautifulSoup:
        resp = await c.get(url, params=params)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "html.parser")

    if client is not None:
        return await _get(client)

    async with httpx.AsyncClient(
        headers=HEADERS, follow_redirects=True, timeout=30
    ) as c:
        return await _get(c)


def _parse_chapters(soup: BeautifulSoup) -> list[dict]:
    """편 목록 파싱 — <select id="sel_01"> 옵션

    반환: [{"no": "3", "name": "제 1편 법령 및 정관"}, ...]
    """
    chapters: list[dict] = []
    # NST HTML에 id 속성이 두 번 선언됨: id="sel_01" id="searchUpperRegltnNo"
    # html.parser는 마지막 id 값(searchUpperRegltnNo)을 사용하므로 해당 값으로 탐색
    sel = soup.find("select", {"id": "searchUpperRegltnNo"})
    if not sel:
        return chapters
    for opt in sel.find_all("option"):
        value = opt.get("value", "").strip()
        text = re.sub(r"[\xa0\s]+", " ", opt.get_text(" ", strip=True)).strip()
        if value and text:
            chapters.append({"no": value, "name": text})
    return chapters


def _parse_regulations(soup: BeautifulSoup) -> list[dict]:
    """규정 목록 파싱 — <ol class="lstBody"> > <li> 구조

    구조: ul.sep > li.col1(번호) · col2(제목) · col3(다운로드) · col4(이력헤더)
    col4는 현재 NST HTML에서 헤더 텍스트만 포함하며 링크 없음.
    """
    regulations: list[dict] = []
    for item in soup.select("ol.lstBody > li"):
        col1 = item.select_one("li.col1")
        col2 = item.select_one("li.col2")
        col3 = item.select_one("li.col3")
        col4 = item.select_one("li.col4")

        number = col1.get_text(strip=True) if col1 else ""
        if not number:
            continue

        # 제목 및 외부 링크 추출
        title = ""
        external_url = None
        is_law_link = False  # 국가법령정보센터 링크 여부
        if col2:
            a = col2.find("a")
            if a:
                raw = a.get_text(" ", strip=True)
                # "* 국가법령정보센터(링크)" 형태 주석 감지 후 제목만 추출
                is_law_link = bool(re.search(r"법령정보|링크", raw))
                title = re.sub(r"\s*\*\s*.+$", "", raw).strip()
                href = a.get("href", "")
                if href.startswith("http"):
                    external_url = href
                elif is_law_link and title:
                    # href="#" 형태: 국가법령정보센터 URL을 법령명으로 직접 생성
                    external_url = f"https://www.law.go.kr/법령/{quote(title)}"
            else:
                raw = col2.get_text(" ", strip=True)
                is_law_link = bool(re.search(r"법령정보|링크", raw))
                title = re.sub(r"\s*\*\s*.+$", "", raw).strip()

        # HWP / PDF 다운로드 링크 추출
        downloads: dict[str, dict] = {}
        search_in = col3 if col3 else item
        for a in search_in.select("a[href*='downloadRegltnBook']"):
            href = a.get("href", "")
            m_no = re.search(r"regltnNo=(\d+)", href)
            m_se = re.search(r"regltnSe=(REGLTN\d+)", href)
            m_ty = re.search(r"atchmnflTy=(\w+)", href)
            if m_no and m_ty:
                ft = m_ty.group(1).lower()
                downloads[ft] = {
                    "url": urljoin(BASE_URL, href.replace("&amp;", "&")),
                    "regltn_no": m_no.group(1),
                    "regltn_se": m_se.group(1) if m_se else None,
                }

        # 개정 이력 링크 추출 (현재 NST HTML에서는 미사용)
        history_url = None
        if col4:
            a = col4.find("a")
            if a:
                href = a.get("href", "")
                if href and href != "#":
                    history_url = urljoin(BASE_URL, href.replace("&amp;", "&"))

        regulations.append({
            "number": number,
            "title": title,
            "external_url": external_url,
            "is_law_link": is_law_link,
            "downloads": downloads,
            "history_url": history_url,
        })
    return regulations


# ─────────────────────────────── MCP Server ────────────────────────────────

server = Server("nst-rulebook")


@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="list_organizations",
            description=(
                "규정집을 제공하는 기관 목록을 반환합니다. "
                "기관 코드(NST / KICA / BRIC)와 이름을 확인할 수 있습니다."
            ),
            inputSchema={"type": "object", "properties": {}, "required": []},
            annotations=types.ToolAnnotations(readOnlyHint=True),
        ),
        types.Tool(
            name="list_chapters",
            description=(
                "특정 기관의 규정집 편(Chapter) 목록을 조회합니다. "
                "각 편의 번호(no)와 이름을 반환합니다. "
                "list_regulations에서 chapter_no로 사용하세요."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "org": {
                        "type": "string",
                        "description": (
                            "기관 코드: NST(국가과학기술연구회), "
                            "KICA(산업기술연구회), BRIC(기초기술연구회). 기본값: NST"
                        ),
                        "enum": ["NST", "KICA", "BRIC"],
                        "default": "NST",
                    }
                },
                "required": [],
            },
            annotations=types.ToolAnnotations(readOnlyHint=True),
        ),
        types.Tool(
            name="list_regulations",
            description=(
                "특정 편의 규정 목록을 조회합니다. "
                "규정 번호, 제목, HWP/PDF 다운로드 링크를 반환합니다. "
                "chapter_no는 list_chapters의 'no' 값을 사용하세요."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "chapter_no": {
                        "type": "string",
                        "description": (
                            "편 번호 (예: '3' = 제1편, '11' = 제3.1편 인사관리). "
                            "list_chapters로 조회 가능."
                        ),
                    },
                    "org": {
                        "type": "string",
                        "description": "기관 코드: NST, KICA, BRIC. 기본값: NST",
                        "enum": ["NST", "KICA", "BRIC"],
                        "default": "NST",
                    },
                },
                "required": ["chapter_no"],
            },
            annotations=types.ToolAnnotations(readOnlyHint=True),
        ),
        types.Tool(
            name="search_regulations",
            description=(
                "특정 기관의 전체 규정을 키워드로 검색합니다. "
                "모든 편을 병렬로 조회하며 제목에서 키워드를 검색합니다."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "keyword": {
                        "type": "string",
                        "description": "검색 키워드 (규정 제목에서 부분 일치 검색)",
                    },
                    "org": {
                        "type": "string",
                        "description": "기관 코드: NST, KICA, BRIC. 기본값: NST",
                        "enum": ["NST", "KICA", "BRIC"],
                        "default": "NST",
                    },
                },
                "required": ["keyword"],
            },
            annotations=types.ToolAnnotations(readOnlyHint=True),
        ),
        types.Tool(
            name="get_download_url",
            description=(
                "규정 파일의 다운로드 URL을 생성합니다. "
                "list_regulations 결과의 regltn_no, regltn_se 값을 사용하세요."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "regltn_no": {
                        "type": "string",
                        "description": "규정 번호 (예: '12'). list_regulations 결과에서 확인.",
                    },
                    "regltn_se": {
                        "type": "string",
                        "description": "규정 구분 코드 (예: 'REGLTN01'). list_regulations 결과에서 확인.",
                    },
                    "file_type": {
                        "type": "string",
                        "description": "파일 형식",
                        "enum": ["hwp", "pdf"],
                        "default": "pdf",
                    },
                },
                "required": ["regltn_no", "regltn_se"],
            },
            annotations=types.ToolAnnotations(readOnlyHint=True),
        ),
        types.Tool(
            name="get_regulation_text",
            description=(
                "규정 PDF 파일을 다운로드하여 본문 텍스트를 추출합니다. "
                "규정 내용을 직접 읽거나 특정 조항을 검색할 때 사용하세요. "
                "list_regulations 결과의 regltn_no, regltn_se 값을 사용하세요."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "regltn_no": {
                        "type": "string",
                        "description": "규정 번호 (예: '91'). list_regulations 결과에서 확인.",
                    },
                    "regltn_se": {
                        "type": "string",
                        "description": "규정 구분 코드 (예: 'REGLTN01'). list_regulations 결과에서 확인.",
                    },
                    "keyword": {
                        "type": "string",
                        "description": "본문에서 찾을 키워드 (선택). 지정 시 해당 키워드 주변 문맥만 반환.",
                    },
                    "max_chars": {
                        "type": "integer",
                        "description": "반환할 최대 글자 수 (기본값: 8000). 전체 본문이 필요하면 크게 설정.",
                        "default": 8000,
                    },
                },
                "required": ["regltn_no", "regltn_se"],
            },
            annotations=types.ToolAnnotations(readOnlyHint=True),
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[types.TextContent]:
    try:
        # ── list_organizations ────────────────────────────────────────────
        if name == "list_organizations":
            lines = ["## 기관 목록\n"]
            for code, info in ORGANIZATIONS.items():
                lines.append(f"- 코드: `{code}` | 이름: {info['name']}")
            return [types.TextContent(type="text", text="\n".join(lines))]

        # ── list_chapters ─────────────────────────────────────────────────
        elif name == "list_chapters":
            org_code = (arguments.get("org") or "NST").upper()
            org = ORGANIZATIONS.get(org_code, ORGANIZATIONS["NST"])
            soup = await fetch_page(
                BASE_URL + "index.do",
                params={"searchRegltn": org["regltn"], "searchUpperRegltnNo": "1"},
            )
            chapters = _parse_chapters(soup)
            if not chapters:
                return [types.TextContent(type="text", text="편 목록을 찾을 수 없습니다.")]
            lines = [
                f"## {org['name']} 규정집 편 목록\n",
                "| 편 번호(chapter_no) | 편 이름 |",
                "|----|------|",
            ]
            for ch in chapters:
                lines.append(f"| `{ch['no']}` | {ch['name']} |")
            return [types.TextContent(type="text", text="\n".join(lines))]

        # ── list_regulations ──────────────────────────────────────────────
        elif name == "list_regulations":
            chapter_no = str(arguments["chapter_no"])
            org_code = (arguments.get("org") or "NST").upper()
            org = ORGANIZATIONS.get(org_code, ORGANIZATIONS["NST"])
            soup = await fetch_page(
                BASE_URL + "index.do",
                params={"searchRegltn": org["regltn"], "searchUpperRegltnNo": chapter_no},
            )

            # 현재 선택된 편 이름 추출 (html.parser는 마지막 id 값 사용)
            chapter_name = chapter_no
            sel = soup.find("select", {"id": "searchUpperRegltnNo"})
            if sel:
                for opt in sel.find_all("option"):
                    if opt.get("selected") is not None:
                        chapter_name = re.sub(
                            r"[\xa0\s]+", " ", opt.get_text(" ", strip=True)
                        ).strip()
                        break

            regs = _parse_regulations(soup)
            if not regs:
                return [types.TextContent(
                    type="text",
                    text=(
                        f"편 번호 `{chapter_no}`에서 규정을 찾을 수 없습니다. "
                        "list_chapters로 올바른 편 번호를 확인하세요."
                    ),
                )]

            lines = [f"## {org['name']} — {chapter_name} 규정 목록\n"]
            for reg in regs:
                lines.append(f"### [{reg['number']}] {reg['title']}")
                if reg["external_url"]:
                    if reg["is_law_link"]:
                        lines.append(
                            f"- 국가법령정보센터(링크): {reg['external_url']}"
                            f"  ※ 이 규정은 국가법령정보센터에서 제공합니다. 링크를 브라우저에서 열어 확인하세요."
                        )
                    else:
                        lines.append(f"- 외부 링크: {reg['external_url']}")
                for ft, info in reg["downloads"].items():
                    lines.append(
                        f"- 다운로드 {ft.upper()}: {info['url']}"
                    )
                if not reg["downloads"] and not reg["external_url"]:
                    lines.append("- 다운로드 링크 없음 (파일 미등록)")
                if reg["history_url"]:
                    lines.append(f"- 개정 이력: {reg['history_url']}")
                lines.append("")
            return [types.TextContent(type="text", text="\n".join(lines))]

        # ── search_regulations ────────────────────────────────────────────
        elif name == "search_regulations":
            keyword = arguments["keyword"]
            org_code = (arguments.get("org") or "NST").upper()
            org = ORGANIZATIONS.get(org_code, ORGANIZATIONS["NST"])

            # 단일 클라이언트 공유 + 세마포어로 동시 연결 수 제한
            async with httpx.AsyncClient(
                headers=HEADERS, follow_redirects=True, timeout=30
            ) as client:
                soup_first = await fetch_page(
                    BASE_URL + "index.do",
                    params={"searchRegltn": org["regltn"], "searchUpperRegltnNo": "1"},
                    client=client,
                )
                chapters = _parse_chapters(soup_first)

                async def fetch_chapter(ch: dict) -> tuple[dict, list[dict]]:
                    async with _SEMAPHORE:
                        try:
                            s = await fetch_page(
                                BASE_URL + "index.do",
                                params={
                                    "searchRegltn": org["regltn"],
                                    "searchUpperRegltnNo": ch["no"],
                                },
                                client=client,
                            )
                            return ch, _parse_regulations(s)
                        except Exception:
                            return ch, []

                results = await asyncio.gather(*[fetch_chapter(ch) for ch in chapters])

            found = [
                (ch, reg)
                for ch, regs in results
                for reg in regs
                if keyword.lower() in reg["title"].lower()
            ]
            if not found:
                return [types.TextContent(
                    type="text", text=f"'{keyword}' 검색 결과가 없습니다."
                )]

            lines = [f"## '{keyword}' 검색 결과 — {org['name']} ({len(found)}건)\n"]
            for ch, reg in found:
                lines.append(f"### [{reg['number']}] {reg['title']}")
                lines.append(f"- 편: {ch['name']} (chapter_no=`{ch['no']}`)")
                for ft, info in reg["downloads"].items():
                    lines.append(f"- 다운로드 {ft.upper()}: {info['url']}")
                if reg["external_url"] and not reg["downloads"]:
                    if reg["is_law_link"]:
                        lines.append(f"- 국가법령정보센터(링크): {reg['external_url']}")
                    else:
                        lines.append(f"- 외부 링크: {reg['external_url']}")
                lines.append("")
            return [types.TextContent(type="text", text="\n".join(lines))]

        # ── get_download_url ──────────────────────────────────────────────
        elif name == "get_download_url":
            regltn_no = arguments["regltn_no"]
            regltn_se = arguments["regltn_se"]
            ft = arguments.get("file_type", "pdf")
            url = (
                BASE_URL
                + f"downloadRegltnBook.do"
                f"?regltnNo={regltn_no}&regltnSe={regltn_se}&atchmnflTy={ft}"
            )
            return [types.TextContent(
                type="text",
                text=f"## 다운로드 URL\n\n- 형식: **{ft.upper()}**\n- URL: {url}",
            )]

        # ── get_regulation_text ───────────────────────────────────────────
        elif name == "get_regulation_text":
            regltn_no = arguments["regltn_no"]
            regltn_se = arguments["regltn_se"]
            keyword = arguments.get("keyword", "").strip()
            max_chars = int(arguments.get("max_chars", 8000))

            pdf_url = (
                BASE_URL
                + f"downloadRegltnBook.do"
                f"?regltnNo={regltn_no}&regltnSe={regltn_se}&atchmnflTy=pdf"
            )

            async with httpx.AsyncClient(
                headers=HEADERS, follow_redirects=True, timeout=60
            ) as client:
                resp = await client.get(pdf_url)
                resp.raise_for_status()

                ct = resp.headers.get("content-type", "")
                if "html" in ct.lower():
                    return [types.TextContent(
                        type="text",
                        text="해당 규정의 PDF 파일을 찾을 수 없습니다. regltn_no, regltn_se 값을 확인하세요.",
                    )]

            # PDF 텍스트 추출
            reader = PdfReader(io.BytesIO(resp.content))
            pages_text: list[str] = []
            for page in reader.pages:
                text = page.extract_text() or ""
                # 줄바꿈 정리: 한글 PDF 특성상 띄어쓰기 없이 붙는 경우가 많아 단어 경계를 보존
                text = re.sub(r"[ \t]+", " ", text)
                pages_text.append(text)

            full_text = "\n".join(pages_text)
            total_pages = len(reader.pages)

            if keyword:
                # 키워드 주변 문맥(앞뒤 300자) 추출
                contexts: list[str] = []
                lower_text = full_text.lower()
                lower_kw = keyword.lower()
                start = 0
                while True:
                    idx = lower_text.find(lower_kw, start)
                    if idx == -1:
                        break
                    ctx_start = max(0, idx - 300)
                    ctx_end = min(len(full_text), idx + len(keyword) + 300)
                    snippet = full_text[ctx_start:ctx_end]
                    contexts.append(f"...{snippet}...")
                    start = idx + 1

                if not contexts:
                    return [types.TextContent(
                        type="text",
                        text=(
                            f"PDF 전체 {total_pages}페이지에서 '{keyword}'를 찾을 수 없습니다.\n\n"
                            f"**다운로드 URL:** {pdf_url}"
                        ),
                    )]

                result = (
                    f"## 규정 본문 검색 결과 — '{keyword}' ({len(contexts)}건)\n"
                    f"(regltn_no=`{regltn_no}`, 총 {total_pages}페이지)\n\n"
                )
                combined = "\n\n---\n\n".join(contexts)
                if len(combined) > max_chars:
                    combined = combined[:max_chars] + "\n\n...(이하 생략)"
                result += combined
            else:
                # 전체 본문 반환 (max_chars 제한)
                result = (
                    f"## 규정 본문 전문\n"
                    f"(regltn_no=`{regltn_no}`, 총 {total_pages}페이지)\n\n"
                )
                if len(full_text) > max_chars:
                    result += full_text[:max_chars] + f"\n\n...(전체 {len(full_text)}자 중 {max_chars}자 표시. max_chars 값을 늘리면 더 볼 수 있습니다.)"
                else:
                    result += full_text

            return [types.TextContent(type="text", text=result)]

        else:
            return [types.TextContent(type="text", text=f"알 수 없는 도구: {name}")]

    except httpx.HTTPStatusError as e:
        return [types.TextContent(
            type="text",
            text=f"HTTP 오류: {e.response.status_code} — {e.request.url}",
        )]
    except httpx.RequestError as e:
        return [types.TextContent(type="text", text=f"네트워크 오류: {e}")]
    except Exception as e:
        return [types.TextContent(type="text", text=f"오류: {type(e).__name__}: {e}")]


# ─────────────────────────────── 진입점 ─────────────────────────────────────

async def main() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


if __name__ == "__main__":
    asyncio.run(main())
