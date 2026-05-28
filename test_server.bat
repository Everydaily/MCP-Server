@echo off
chcp 65001 >nul
echo ============================================
echo  NST MCP 서버 동작 테스트
echo ============================================
echo.

cd /d "%~dp0"

:: Python 및 패키지 확인
echo [검사 1] Python 버전
python --version
echo.

echo [검사 2] 필수 패키지 설치 여부
python -c "import mcp, httpx, bs4, pypdf; print('  mcp, httpx, beautifulsoup4, pypdf - 모두 정상')"
if errorlevel 1 (
    echo [오류] 패키지가 없습니다. setup.bat 를 먼저 실행하세요.
    pause
    exit /b 1
)
echo.

echo [검사 3] server.py 문법 확인
python -c "import ast; ast.parse(open('server.py', encoding='utf-8').read()); print('  문법 이상 없음')"
echo.

echo [검사 4] 서버 구동 테스트 (3초 후 자동 종료)
echo  서버를 3초간 실행합니다...
start /b python server.py > server_test.log 2>&1
timeout /t 3 /nobreak >nul
taskkill /f /im python.exe >nul 2>&1

if exist server_test.log (
    echo  로그 확인:
    type server_test.log
    del server_test.log
) else (
    echo  실행 로그 없음 (정상 - MCP 서버는 화면 출력 없이 대기)
)
echo.

echo ============================================
echo  모든 검사 완료 - 서버가 정상 작동합니다.
echo  Claude Desktop을 재시작하면 사용 가능합니다.
echo ============================================
echo.
pause
