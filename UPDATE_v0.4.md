# 백엔드 v0.4 — 실험 데이터 연동

기존 backend 레포에 이 ZIP의 backend 폴더 내용을 적용합니다. 프론트 변경은 없습니다. 실행 중인 서버를 종료하고 다음을 실행합니다.

```bash
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
python -m uvicorn app.server:app --host 127.0.0.1 --port 8000
```

기본 DB가 `data/backend_v04.sqlite3`로 바뀌었습니다. 최초 시작 시 CSV를 다시 적재하며 기존 DB는 수정하지 않습니다. 기존 이벤트·보고를 새 DB로 이관하지 않습니다. `DB_PATH`를 이전 DB로 설정해 둔 경우 해제하거나 새 경로로 바꾸세요. 새 DB에서 sample, calibration, experiment를 사용할 수 있습니다. 기존 프론트에서 실험 CSV를 고른 뒤 새 세션을 생성하세요. 학습 결과는 서버 판별에 자동 적용되지 않습니다.

- `node + ts` 중복 키, seq=0 허용. 동일 키의 충돌은 덮어쓰지 않고 오류 처리합니다.
- v3 B 보정 적용. PM 무보정, A 습도 표시 전용, B 습도 제외. 원본 차압은 화면에 그대로, 분석에서는 부호 반전. 11/17 16:48 KST 전 모션 제외.
- `GET /api/v1/datasets/{dataset_id}/labels`: 고정본 데이터셋의 회차와 제외 플래그입니다. CANCEL 및 오염된 파일럿을 평가지표에서 제외하며 API에는 검토용으로 표시합니다.
- `python -m app.evaluate`: 본실험 70회 leave-one-run-out 오프라인 평가를 재현합니다. 라벨 구간 안의 최초 5초(차압은 최초 10초 제외 후)를 변화 기준으로 사용합니다. 실험 직전의 깨끗한 기준 구간을 확보한 것은 아닙니다. run 48 PM·run 79 A 모션 제외는 오프라인 평가에만 적용합니다.
- SGP30 정상 회차 재정렬: 관측 시작 또는 관측된 재시작 후 12시간 지난 양쪽 값이 있는 정상 회차 10개에서 v3 대비 B 추가 오프셋 후보를 계산했습니다. 이는 챔버 내 실제 위치 차이와 센서 편차를 분리하지 못하므로 운영 화면이나 모델에 자동 적용하지 않습니다. overnight CSV는 v3 출처 확인용이며 실험 학습에 쓰지 않습니다.
- 실험 CSV·라벨·개별 회차 평가 JSON은 팀 내부 사용 자료이며 `.gitignore`에 추가했습니다. 이미 추적 중인 파일의 공개는 별도로 확인하세요. GitHub에 직접 푸시하지 않았습니다.
