# 백엔드 폴더 구조

이 문서는 백엔드 레포의 초기 폴더 역할을 설명합니다.
현재 레포에는 실제 기능 코드가 없으며, 각 담당자가 작업을 시작할 수 있도록 빈 구조만 준비되어 있습니다.

## 전체 구조

```txt
backend/
  app/
    routers/
    services/
    repositories/
    models/
    schemas/
    utils/
    core/
  uploads/
  data/
  tests/
  docs/
    specs/
    diagrams/
    meeting-notes/
  .gitignore
  README.md
```

## app

- `app/routers`: API 라우터 작성 위치. documents, ai, ocr, voice, archive, profile 등으로 나눌 예정
- `app/services`: 핵심 비즈니스 로직 작성 위치. 문서 처리, AI 요청, OCR 처리, 음성 명령 처리 등
- `app/repositories`: DB 접근 로직 작성 위치
- `app/models`: SQLAlchemy 모델 작성 위치
- `app/schemas`: Pydantic 요청/응답 스키마 작성 위치
- `app/utils`: 파일 처리, 텍스트 처리, 공통 응답 등 유틸 함수 위치
- `app/core`: 환경 설정, CORS, 공통 설정 위치

## 저장소

- `uploads`: 업로드된 원본 문서, 이미지 파일 저장 위치. 실제 파일은 Git에 올리지 않음
- `data`: SQLite DB 파일 저장 위치. 실제 DB 파일은 Git에 올리지 않음

## 테스트

- `tests`: 백엔드 테스트 코드 위치

## docs

- `docs/specs`: 백엔드 API 명세, 데이터 모델 명세, 기능 정의서 보관 위치
- `docs/diagrams`: 서버 아키텍처 구조도, DB ERD, API 흐름도 보관 위치
- `docs/meeting-notes`: 백엔드 회의록, 의사결정 기록 보관 위치
