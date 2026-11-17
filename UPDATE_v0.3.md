# v0.3 규칙 엔진 · 이벤트 API

## 적용
기존 backend 저장소 루트에 이 폴더 내용을 적용합니다. 프론트 저장소 파일은 없습니다. 서버를 Ctrl+C로 종료한 뒤 다음을 실행합니다.

```bash
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
python -m uvicorn app.server:app --host 127.0.0.1 --port 8000
```

## 구현
- 데이터셋 kind=experiment일 때만 활성 가능한 설정 기반 규칙 엔진
- value/delta/rate/missing_seconds 특징과 AND 조건, 조건 지속시간, 회복 지속시간
- 실제 재생 원본을 순서대로 모두 처리(배속·화면 polling 간격과 독립)
- 반복 이상 이벤트 병합, 회복·중지·데이터 종료·seek 경계 처리
- 이벤트 목록/상세/처리 보고, expected_version 충돌 방지
- SQLite analysis_events에 이벤트 및 처리 보고 JSON 보존
- 기존 snapshot에 분석 상태와 활성 이벤트 수 반영
- 동시 상태 변경 직렬화 및 기존 CSV 재생 계약 유지

## 의도적으로 꺼 둔 기능
rules.json은 enabled=false, rules=[]입니다. 실험 CSV와 라벨이 없으므로 실제 판별 임계값이나 정확도를 지어내지 않았습니다. sample/calibration에는 enabled=true여도 분석하지 않습니다. 따라서 기존 두 CSV로 실행하면 계속 분석 미지원으로 보입니다. 이것은 오류가 아닙니다.

rules.example.json은 설정 문법을 보여주기 위한 **임의 수치 예제**이며 실험에서 도출한 기준이 아닙니다. 그대로 운영하거나 보정 데이터를 정상 학습 데이터로 바꾸지 마세요. 설정을 활성화하려면 실험으로 규칙을 검증하고 rules.json에 반영한 뒤 새 세션을 생성합니다. 설정은 세션마다 고정됩니다. 빈 설정에서는 원인 판별이 완료된 것으로 보이지 않습니다.

5종 원인 코드를 모두 설정으로 매핑할 수 있지만 5종을 실제로 구별하는 규칙은 아직 확정하지 않았습니다. B→A 반응 시간차 전용 특징, 전역 비교 자동화, 고정값/급변 센서 이상 규칙, 학습 모델, 라벨 API는 후속 구현 대상입니다. 현재 조건 기능으로 이것들이 검증됐다고 주장하지 않습니다.

## 새 API (기본 /api/v1)
- GET /sessions/{sid}/events: 현재 generation 이벤트 배열. 성공 {data,meta}. 우선순위 내림차순. 필터·pagination 미지원.
- GET /sessions/{sid}/events/{eid}: Event 객체
- PATCH /sessions/{sid}/events/{eid}: {handling_status, expected_version, note}
  - new→acknowledged/in_progress/resolved
  - acknowledged→in_progress/resolved, in_progress→resolved
  - 동일 상태·역전이 409. version 불일치 409. 메모 2000자 초과 422.
  - 예시: {"handling_status":"in_progress","expected_version":1,"note":"현장 확인 중"}
  - 응답 Event 전체. 메모 갱신만 하는 API는 아직 없음.

보고 상태와 센서 이상 회복은 별개입니다. confidence는 항상 null이며 confidence_kind=none입니다. rule_version/rule_sha256/validation_status=unvalidated를 반환합니다. 여러 원인이 동시에 활성화되면 snapshot cause=null이며 상세 원인은 events를 조회합니다. 조건 미충족은 normal 대신 no_rule_match입니다. 근거 불충분은 uncertain입니다. 정상 분류가 검증된 것이 아닙니다.

priority.score는 설정자가 지정한 검증 전 고정 점수입니다. configured_unvalidated로 표시하며 components=[]입니다. 기획서의 가중치 기반 위험도 점수는 아직 구현하지 않았습니다. 이것을 과학적 위험도나 실제 평가 결과로 발표하지 마세요.

end_reason은 recovered/session_stopped/dataset_ended/seek_reset 중 하나입니다. rule 특징 단위는 feature에 따라 다르므로 evidence.unit은 현재 null입니다. 이를 임의 ppm으로 표시하면 안 됩니다.

## 프론트 연계
현재 프론트는 분석 미지원 안내를 갖고 있습니다. 이벤트 화면·보고 저장 연결은 프론트 담당자가 신규 API에 연결해야 하며 이 백엔드 변경만으로 화면에 이벤트가 생기지 않습니다. SSE는 기존대로 snapshot.updated만 보내므로 이벤트는 REST 재조회가 필요합니다. 제안 API 명세와 구현 명세가 다르며 실제 계약은 openapi-implemented.json과 이 문서를 따릅니다.

## 보존·제약
세션과 재생 상태는 메모리이며 재시작 시 사라집니다. DB의 이벤트·보고 JSON은 남지만 이전 세션을 재개하거나 과거 세션 이벤트를 조회하는 API는 아직 없습니다. 현재 generation 이벤트만 조회합니다. 단일 worker·내부망 개발용이며 인증·실사용자 감사 기록은 없습니다.

## 검증
기존 15개 + 추가 6개 = 총 21개 테스트 통과. 추가 검증은 합성 데이터 기반 이벤트 병합·회복·delta·결측 시 회복 오인 방지·습도 설정 거부·보고 충돌·DB 보존·seek 격리입니다. 5종 코드 테스트는 코드 매핑 검증이지 원인 분류 정확도 검증이 아닙니다. 브라우저 E2E·실제 실험 분류·현장 MQTT는 미검증입니다.
