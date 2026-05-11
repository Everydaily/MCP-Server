#!/usr/bin/env python3
"""NST 규정집 MCP 서버 - 국가과학기술연구회 규정집 게시판 연동

URL 파라미터 구조:
  searchRegltn       : 기관 코드 (REGLTN01=NST, REGLTN02=KICA, REGLTN03=BRIC)
  searchUpperRegltnNo: 편 번호 (1, 3, 7, 11, ... — select 옵션 value)
"""

import asyncio
import re
from typing import Any
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types

BASE_URL = "https://www.nst.re.kr/rulebook/"

# 기관 코드 → searchRegltn 파라미터
ORGANIZATIONS = {
    "NST":  {"name": "국가과학기술연구회", "regltn": "REGLTN01"},
    "KICA": {"name": "산업기술연구회",     "regltn": "REGLTN02"},
    "BRIC": {"name": "기초기술연구회",     "regltn": "REGLTN03"},
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
    "Referer": "https://www.nst.re.kr/",
}


async def fetch_page(url: str, params: dict | None = None) -> BeautifulSoup:
    async with httpx.AsyncClient(headers=HEADERS, follow_redirects=True, timeout=30) as client:
        resp = await client.get(url, params=params)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "lxml")


def _parse_chapters(soup: BeautifulSoup) -> list[dict]:
    """편 목록 파싱 — <select id="sel_01"> 옵션 사용
    반환: [{"no": "3", "name": "제 1편 법령 및 정관"}, ...]
    """
    chapters = []
    sel = soup.find("select", {"id": "sel_01"})
    if not sel:
        return chapters
    for opt in sel.find_all("option"):
        value = opt.get("value", "").strip()
        text = opt.get_text(" ", strip=True).replace("\xa0", " ").strip()
        if value and text:
            chapters.append({"no": value, "name": text})
    return chapters


def _parse_regulations(soup: BeautifulSoup) -> list[dict]:
    """규정 목록 파싱 — <ol class="lstBody"> > <li> 구조
    각 li 안: ul.sep > li.col1(번호) col2(제목) col3(다운로드) col4(이력)
    """
    regulations = []
    for item in soup.select("ol.lstBody > li"):
        col1 = item.select_one("li.col1")
        col2 = item.select_one("li.col2")
        col3 = item.select_one("li.col3")
        col4 = item.select_one("li.col4")

        number = col1.get_text(strip=True) if col1 else ""
        if not number:
            continue

        # 제목과 외부 링크
        title = ""
        external_url = None
        if col2:
            a = col2.find("a")
            if a:
                raw = a.get_text(" ", strip=True)
                # "* 국가법령정보센터(링크)" 같은 주석 제거
                title = re.sub(r"\s*\*\s*.+$", "", raw).strip()
                href = a.get("href", "")
                if href.startswith("http"):
                    external_url = href
            else:
                raw = col2.get_text(" ", strip=True)
                title = re.sub(r"\s*\*\s*.+$", "", raw).strip()

        # 다운로드 링크 (col3)
        downloads = {}
        search_in = col3 if col3 else item
        for a in search_in.select("a[href*='downloadRegltnBook']"):
            href = a.get("href", "")
            m_no = re.search(r"regltnNo=(\d+)", href)
            m_se = re.search(r"regltnSe=(REGLTN\d+)", href)
            m_ty = re.search(r"atchmnflTy=(\w+)", href)
            if m_no and m_ty:
                ft = m_ty.group(1).lower()
                downloads[ft] = {
                    "url": urljoin(BASE_URL, href),
                    "regltn_no": m_no.group(1),
                    "regltn_se": m_se.group(1) if m_se else None,
                }

        # 이력 링크 (col4)
        history_url = None
        if col4:
            a = col4.find("a")
            if a:
                href = a.get("href", "")
                if href and href != "#":
                    history_url = urljoin(BASE_URL, href)

        regulations.append({
            "number": number,
            "title": title,
            "external_url": external_url,
            "downloads": downloads,
            "history_url": history_url,
        })
    return regulations


# ────────────────────────────── MCP Server ──────────────────────────────

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
                        "description": "기관 코드: NST(국가과학기술연구회), KICA(산업기술연구회), BRIC(기초기술연구회). 기본값: NST",
                        "enum": ["NST", "KICA", "BRIC"],
                        "default": "NST",
                    }
                },
                "required": [],
            },
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
                        "description": "편 번호 (예: '3' = 제1편, '7' = 제2편). list_chapters로 조회 가능.",
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
        ),
        types.Tool(
            name="search_regulations",
            description=(
                "특정 기관의 전체 규정을 키워드로 검색합니다. "
                "모든 편을 순회하며 제목에서 키워드를 검색합니다."
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
                        "description": "규정 번호 (예: '6'). list_regulations 결과에서 확인.",
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
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[types.TextContent]:
    try:
        if name == "list_organizations":
            lines = ["## 기관 목록\n"]
            for code, info in ORGANIZATIONS.items():
                lines.append(f"- 코드: `{code}` | 이름: {info['name']}")
            return [types.TextContent(type="text", text="\n".join(lines))]

        elif name == "list_chapters":
            org_code = arguments.get("org", "NST")
            org = ORGANIZATIONS.get(org_code, ORGANIZATIONS["NST"])
            # 아무 편 번호나 지정해서 페이지 로드 (select 옵션은 항상 전체 표시)
            soup = await fetch_page(
                BASE_URL + "index.do",
                params={"searchRegltn": org["regltn"], "searchUpperRegltnNo": "1"},
            )
            chapters = _parse_chapters(soup)
            if not chapters:
                return [types.TextContent(type="text", text="편 목록을 찾을 수 없습니다.")]
            lines = [f"## {org['name']} 규정집 편 목록\n",
                     "| 편 번호(chapter_no) | 편 이름 |",
                     "|----|------|"]
            for ch in chapters:
                lines.append(f"| `{ch['no']}` | {ch['name']} |")
            return [types.TextContent(type="text", text="\n".join(lines))]

        elif name == "list_regulations":
            chapter_no = arguments["chapter_no"]
            org_code = arguments.get("org", "NST")
            org = ORGANIZATIONS.get(org_code, ORGANIZATIONS["NST"])
            soup = await fetch_page(
                BASE_URL + "index.do",
                params={"searchRegltn": org["regltn"], "searchUpperRegltnNo": chapter_no},
            )
            # 현재 선택된 편 이름 추출
            chapter_name = ""
            sel = soup.find("select", {"id": "sel_01"})
            if sel:
                for opt in sel.find_all("option"):
                    if opt.get("selected"):
                        chapter_name = opt.get_text(" ", strip=True).replace("\xa0", " ").strip()
                        break

            regs = _parse_regulations(soup)
            if not regs:
                return [types.TextContent(
                    type="text",
                    text=(f"편 번호 `{chapter_no}`에서 규정을 찾을 수 없습니다. "
                          "list_chapters로 올바른 편 번호를 확인하세요."),
                )]
            lines = [f"## {org['name']} — {chapter_name or chapter_no} 규정 목록\n"]
            for reg in regs:
                lines.append(f"### [{reg['number']}] {reg['title']}")
                if reg["external_url"]:
                    lines.append(f"- 외부 링크: {reg['external_url']}")
                for ft, info in reg["downloads"].items():
                    lines.append(
                        f"- 다운로드 {ft.upper()}: {info['url']}  "
                        f"(regltn_no=`{info['regltn_no']}`, regltn_se=`{info['regltn_se']}`)"
                    )
                if reg["history_url"]:
                    lines.append(f"- 개정 이력: {reg['history_url']}")
                lines.append("")
            return [types.TextContent(type="text", text="\n".join(lines))]

        elif name == "search_regulations":
            keyword = arguments["keyword"]
            org_code = arguments.get("org", "NST")
            org = ORGANIZATIONS.get(org_code, ORGANIZATIONS["NST"])

            soup_first = await fetch_page(
                BASE_URL + "index.do",
                params={"searchRegltn": org["regltn"], "searchUpperRegltnNo": "1"},
            )
            chapters = _parse_chapters(soup_first)

            async def fetch_chapter(ch: dict) -> tuple[dict, list[dict]]:
                try:
                    s = await fetch_page(
                        BASE_URL + "index.do",
                        params={"searchRegltn": org["regltn"], "searchUpperRegltnNo": ch["no"]},
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
                return [types.TextContent(type="text", text=f"'{keyword}' 검색 결과가 없습니다.")]

            lines = [f"## '{keyword}' 검색 결과 — {org['name']} ({len(found)}건)\n"]
            for ch, reg in found:
                lines.append(f"### [{reg['number']}] {reg['title']}")
                lines.append(f"- 편: {ch['name']} (chapter_no=`{ch['no']}`)")
                for ft, info in reg["downloads"].items():
                    lines.append(f"- 다운로드 {ft.upper()}: {info['url']}")
                lines.append("")
            return [types.TextContent(type="text", text="\n".join(lines))]

        elif name == "get_download_url":
            regltn_no = arguments["regltn_no"]
            regltn_se = arguments["regltn_se"]
            ft = arguments.get("file_type", "pdf")
            url = (
                BASE_URL
                + f"downloadRegltnBook.do?regltnNo={regltn_no}"
                f"&regltnSe={regltn_se}&atchmnflTy={ft}"
            )
            return [types.TextContent(
                type="text",
                text=f"## 다운로드 URL\n\n- 형식: **{ft.upper()}**\n- URL: {url}",
            )]

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


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


if __name__ == "__main__":
    asyncio.run(main())
