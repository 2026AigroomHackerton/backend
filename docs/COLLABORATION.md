# 백엔드 협업 규칙

## 1. 기본 원칙

이 레포는 AI Mobile Document Assistant의 백엔드 작업 공간입니다.
현재는 초기 구조만 있으며, 실제 FastAPI 프로젝트 설정과 기능 구현은 이후 작업에서 진행합니다.

## 2. 담당 영역 예시

- `app/routers`: API 라우터
- `app/services`: 문서 처리, AI 요청, OCR 처리, 음성 명령 처리 등 핵심 로직
- `app/repositories`: DB 접근 로직
- `app/models`: SQLAlchemy 모델
- `app/schemas`: Pydantic 요청/응답 스키마
- `app/utils`: 파일 처리, 텍스트 처리, 공통 응답 유틸
- `app/core`: 환경 설정, CORS, 공통 설정
- `tests`: 백엔드 테스트

## 3. 브랜치 규칙

브랜치명 예시:

- `feature/backend-documents`
- `feature/ocr-service`
- `feature/ai-service`
- `feature/archive-api`
- `feature/profile-api`
- `docs/backend-spec-update`

## 4. 커밋 메시지 규칙

예시:

- `chore: 백엔드 초기 폴더 구조 추가`
- `feat: 문서 업로드 API 추가`
- `feat: OCR 처리 서비스 추가`
- `feat: 문서 분석 API 추가`
- `fix: 스키마 오류 수정`
- `docs: 백엔드 명세 업데이트`

## 5. 충돌 방지 규칙

- 같은 라우터나 서비스 파일을 여러 명이 동시에 수정하지 않는다.
- `app/schemas` 수정 전 프론트엔드 담당자에게 공유한다.
- API 응답 구조 변경 시 프론트/백엔드 담당자 모두에게 공유한다.
- DB 모델 변경 시 관련 repository와 schema 영향 범위를 확인한다.
- Pull 전에 항상 최신 main을 가져온다.
- Merge 전 서버 실행과 API 응답 확인을 한다.

## 6. AI 활용 주의사항

- AI가 만든 파일이 기존 폴더 역할과 맞는지 확인한다.
- 같은 역할의 라우터, 서비스, 스키마를 중복 생성하지 않는다.
- API 경로를 임의로 바꾸지 않는다.
- 명세서와 다른 응답 구조를 만들 경우 반드시 공유한다.
