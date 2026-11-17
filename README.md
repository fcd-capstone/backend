> 최신 규칙 엔진·이벤트 API 및 남은 범위는 **UPDATE_v0.3.md**를 먼저 확인하세요. 아래 이전 버전의 분석 미구현 설명은 규칙 실행 기반 추가 전 기록입니다.

# FCD Backend

프론트와 독립된 FastAPI 백엔드 저장소입니다. 현재 CSV 재생·조회 연동 버전이며 최종 판별 시스템은 아닙니다.

## 실행 (Python 3.10 이상)

```bash
python -m venv .venv
# Windows: .venv\Scripts\Activate.ps1
# macOS/Linux: source .venv/bin/activate
python -m pip install -r requirements.txt
python -m uvicorn app.server:app --host 127.0.0.1 --port 8000
```

첫 실행 시 data 폴더의 CSV 2개를 자동 적재합니다. /docs에서 API 확인. 기본 URL은 http://localhost:8000/api/v1 입니다. 반드시 app.server:app을 실행하세요.

프론트 localhost:5173 및 127.0.0.1:5173에 CORS를 허용합니다. 다른 프론트 주소는 app/server.py에서 설정하세요. 프론트는 별도 저장소·별도 프로세스로 실행합니다.

## 실제 구현

데이터셋 목록, 세션 생성·조회, 서버 시계 기반 CSV 재생(play/pause/stop/seek/배속), snapshot, 시계열 집계, snapshot SSE. 구현 HTTP 명세는 openapi-implemented.json입니다. SSE는 snapshot.updated만 제공하고 재연결 이벤트 이력은 없습니다. 세션은 메모리이며 재시작 시 삭제됩니다. 단일 worker 개발용입니다.

B 습도 제외, A 습도 카드 전용, v2 보정, 보정용 첫 30분 분석 제외, CO2 포화 표시·집계 제외를 반영했습니다. 차압 영점 적용 방향은 확인 전이라 미적용입니다.

## 미구현

MQTT/live, 원인 판별·학습·우선순위, 조치 보고·라벨 API, 인증, 전체 제안 계약의 표준 오류·페이지 처리. events는 빈 배열을 반환하며 정상 판정이라는 뜻이 아닙니다. 판별 결과를 가짜 생성하지 않습니다.

## 테스트

```bash
python -m unittest discover -s tests -v
```

핵심 테스트 7개 및 HTTP TestClient 연동 검증을 통과했습니다. 브라우저 E2E·현장 시험은 수행하지 않았습니다.

## 새 GitHub 저장소에 push

GitHub에서 빈 백엔드 저장소를 만든 뒤 아래 YOUR_BACKEND_REPOSITORY_URL을 실제 주소로 바꾸세요.

```bash
git init
git branch -M main
git add .
git diff --cached --stat
git commit -m "feat: initialize CSV replay backend"
git remote add origin YOUR_BACKEND_REPOSITORY_URL
git push -u origin main
```

공개 저장소라면 측정 CSV 공개 가능 여부를 먼저 확인하세요. 공개하지 않을 경우 data/*.csv를 .gitignore에 추가하고 실행할 컴퓨터에만 파일을 배치하세요. 로컬 SQLite DB 및 가상환경은 커밋하지 않습니다.


## 프론트 연동 회귀 검증 (2026-11-17)

최신 front의 src/data/api.ts 및 docs/api-coordination.md와 대조했습니다. 기존 API 경로와 응답을 유지합니다.

```bash
python -m pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
```

총 15개 테스트(기존 7+HTTP 연동 8)가 통과했습니다. HTTP 테스트는 임시 DB에 동봉 CSV를 적재하며 기존 로컬 DB를 변경하지 않습니다. 프론트 필수 필드, CORS, 보정·습도 제외, seek 초기화, 재생 상태, 시계열 요청 제한, SSE 첫 프레임을 검사합니다. 브라우저 E2E, SSE 장시간 재연결, 실제 현장 시험은 미검증입니다.

추가 변경: snapshot은 노드별 시간 인덱스를 이진 탐색하여 전체 CSV 반복 순회를 피하고, 시계열은 요청 구간만 검색합니다. 메모리 세션·단일 worker 제약은 동일합니다. DB_PATH와 CORS_ORIGINS 환경변수를 지원합니다. 수치 요청에서 NaN/Infinity를 거절합니다. CORS_ORIGINS는 쉼표로 구분한 프론트 origin 목록입니다. 기본은 localhost:5173 및 127.0.0.1:5173입니다. .env 파일은 자동 로딩하지 않습니다.
