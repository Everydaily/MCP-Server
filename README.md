# NST 규정집 MCP 서버

국가과학기술연구회(NST), 산업기술연구회(KICA), 기초기술연구회(BRIC) 규정집을  
**Claude · ChatGPT · Gemini**에 연동하는 MCP(Model Context Protocol) 서버

---

## 제공 도구

| 도구 | 설명 |
|------|------|
| `list_organizations` | 기관 목록 반환 (NST / KICA / BRIC) |
| `list_chapters` | 기관별 규정집 편(Chapter) 목록 조회 |
| `list_regulations` | 특정 편의 규정 목록 + HWP/PDF 다운로드 링크 |
| `search_regulations` | 키워드로 전체 규정 검색 (병렬 조회) |
| `get_download_url` | 규정 파일 다운로드 URL 생성 |

---

## 설치

```bash
# 1. 저장소 클론
git clone https://github.com/Everydaily/MCP-Server.git
cd MCP-Server

# 2. 의존성 설치
pip install -r requirements.txt
```

**의존성:** `mcp` · `httpx` · `beautifulsoup4` (모두 표준 pip 설치 가능)

---

## Claude Desktop 연동

`%APPDATA%\Claude\claude_desktop_config.json` 파일에 추가:

```json
{
  "mcpServers": {
    "nst-rulebook": {
      "command": "python",
      "args": ["C:/Users/<사용자명>/MCP-Server/server.py"]
    }
  }
}
```

> macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`

Claude Desktop을 재시작하면 🔧 도구 아이콘이 나타납니다.

---

## Claude Code (CLI) 연동

```bash
claude mcp add nst-rulebook python C:/Users/<사용자명>/MCP-Server/server.py
claude mcp list   # 등록 확인
```

---

## 사용 예시

```
NST 규정집 편 구성 보여줘
국가과학기술연구회 인사관련 규정 찾아줘
산업기술연구회 제4편 인사관리 규정 목록 보여줘
인사규정 PDF 다운로드 링크 알려줘
```

---

## ChatGPT / Gemini 연동

Function Calling 방식으로 직접 연동할 수 있습니다.  
각 AI별 예제 코드는 [nst-rulebook-mcp](../nst-rulebook-mcp/guides/) 폴더를 참고하세요.

---

## 기술 스택

- **MCP SDK** — `mcp` (stdio transport)
- **HTTP 클라이언트** — `httpx` (비동기)
- **HTML 파서** — `beautifulsoup4` + `html.parser` (표준 내장)
- **데이터 출처** — [www.nst.re.kr/rulebook](https://www.nst.re.kr/rulebook/index.do)
