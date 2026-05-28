@echo off
chcp 65001 >nul
echo ============================================
echo  NST 규정집 MCP 서버 설치 스크립트
echo ============================================
echo.

:: Python 확인
python --version >nul 2>&1
if errorlevel 1 (
    echo [오류] Python이 설치되지 않았습니다.
    echo https://www.python.org/downloads/ 에서 Python 3.10 이상을 설치하세요.
    echo 설치 시 "Add Python to PATH" 반드시 체크!
    pause
    exit /b 1
)

echo [1/3] Python 버전 확인...
python --version

:: 현재 스크립트 위치로 이동
cd /d "%~dp0"
echo.
echo [2/3] 필수 패키지 설치 중...
pip install -r requirements.txt
if errorlevel 1 (
    echo [오류] 패키지 설치 실패. 인터넷 연결 확인 후 다시 시도하세요.
    pause
    exit /b 1
)

echo.
echo [3/3] Claude Desktop 설정 파일 확인...
set CONFIG_PATH=%APPDATA%\Claude\claude_desktop_config.json
if not exist "%CONFIG_PATH%" (
    echo [안내] Claude Desktop 설정 파일이 없습니다.
    echo Claude Desktop을 먼저 실행한 후 다시 시도하세요.
    pause
    exit /b 1
)

echo.
echo ============================================
echo  설치 완료!
echo ============================================
echo.
echo 다음 단계:
echo  1. %APPDATA%\Claude\claude_desktop_config.json 파일을 메모장으로 열기
echo  2. 아래 내용을 "mcpServers" 항목에 추가:
echo.
echo     "nst-rulebook": {
echo       "command": "python",
echo       "args": ["%~dp0server.py"]
echo     }
echo.
echo  3. Claude Desktop 재시작
echo.
pause
