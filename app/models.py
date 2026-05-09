"""
SQLAlchemy 2.x ORM 모델 정의 — 명세 9.2 기준 15개 테이블.

[설계 원칙]
- DeclarativeBase + Mapped[...] / mapped_column 신 스타일.
- datetime 컬럼은 ISO-8601 문자열로 보관 (기존 raw SQL 코드와 호환).
  → Mapped[str] 로 매핑. SQLAlchemy 가 자동 직렬화하지 않으므로 호출부에서
    `datetime.now(timezone.utc).isoformat()` 형태로 채워 넣는다.
- JSON 형태 데이터(target_json/raw_meta_json/source_ids 등)는 SQLite 가 native JSON
  을 지원하지 않으므로 TEXT 로 저장 (Mapped[str | None]).
- ForeignKey 제약은 명시하지만 SQLite 기본 enforcement 가 OFF 라
  실제 강제는 connection 단에서 `PRAGMA foreign_keys = ON` 켤 때만 적용된다.

[기존 raw SQL 스키마와의 호환]
- documents 테이블은 services/document_service.py::_init_db() 가 먼저 만든
  컬럼 집합(user_id, original_filename 등)을 보유한다.
- 본 모델은 spec 컬럼(owner_type, owner_id, file_type) 을 추가로 정의하며,
  database.py 의 init_db() 가 ALTER TABLE 로 누락 컬럼만 보강한다.
- document_versions 도 기존 컬럼 + spec 의 file_path 추가 형태.

[명세 외 보조 컬럼]
- 명세 체크리스트는 "필수 컬럼"만 나열하므로, 본 모델은 명세 키를 모두 포함하면서
  기존 코드가 의존하는 컬럼(예: documents.user_id, file_extension) 도 함께 매핑한다.
"""

from __future__ import annotations

from sqlalchemy import ForeignKey, Integer, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


# =============================================================================
# Declarative Base
# =============================================================================
# database.py 가 본 클래스를 import 해 metadata.create_all() 을 호출한다.
class Base(DeclarativeBase):
    """프로젝트 공용 SQLAlchemy 2.x DeclarativeBase."""


# =============================================================================
# 9.2.1 demo_users
# =============================================================================
class DemoUser(Base):
    __tablename__ = "demo_users"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    email: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)


# =============================================================================
# 9.2.2 demo_profiles
# =============================================================================
class DemoProfile(Base):
    __tablename__ = "demo_profiles"

    # 명세상 user_id 가 PK 역할 (1:1 프로필).
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("demo_users.id"), primary_key=True
    )
    name_ko: Mapped[str | None] = mapped_column(Text, nullable=True)
    phone: Mapped[str | None] = mapped_column(Text, nullable=True)
    email: Mapped[str | None] = mapped_column(Text, nullable=True)
    address: Mapped[str | None] = mapped_column(Text, nullable=True)


# =============================================================================
# 9.2.3 folders
# =============================================================================
class Folder(Base):
    __tablename__ = "folders"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    # owner_type: "user" | "group" 등. 추후 enum 으로 강제 가능.
    owner_type: Mapped[str] = mapped_column(Text, nullable=False)
    owner_id: Mapped[int] = mapped_column(Integer, nullable=False)
    # 트리 구조 — 자기참조 FK. NULL 이면 루트 폴더.
    parent_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("folders.id"), nullable=True
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)


# =============================================================================
# 9.2.4 groups
# =============================================================================
class Group(Base):
    __tablename__ = "groups"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    # 초대 코드 — 짧은 무작위 문자열. UNIQUE 제약은 운영 단계에서 추가.
    invite_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    owner_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("demo_users.id"), nullable=False
    )
    created_at: Mapped[str] = mapped_column(Text, nullable=False)


# =============================================================================
# 9.2.5 group_members
# =============================================================================
class GroupMember(Base):
    __tablename__ = "group_members"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    group_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("groups.id"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("demo_users.id"), nullable=False
    )
    # role: "owner" | "editor" | "viewer" 등. 운영 시 enum 도입 권장.
    role: Mapped[str] = mapped_column(Text, nullable=False, default="viewer")
    # SQLite 의 boolean 은 INTEGER 0/1. SQLAlchemy 가 bool ↔ int 자동 변환.
    can_read: Mapped[bool] = mapped_column(default=True)
    can_edit: Mapped[bool] = mapped_column(default=False)
    can_delete: Mapped[bool] = mapped_column(default=False)


# =============================================================================
# 9.2.6 documents
# =============================================================================
# 기존 _init_db() 가 만들어 둔 컬럼(user_id/original_filename/...) + 명세 컬럼
# (owner_type/owner_id/file_type) 을 모두 정의한다.
# database.py::init_db() 가 ALTER TABLE 로 누락 컬럼만 보강한다.
class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    # ---- 명세 9.2.6 핵심 컬럼 ----
    owner_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    owner_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    folder_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("folders.id"), nullable=True
    )
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_type: Mapped[str] = mapped_column(Text, nullable=False, default="upload")
    file_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    parse_status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    deleted_at: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ---- 기존 _init_db() / repository 코드 호환을 위한 보조 컬럼 ----
    # user_id 는 owner_id 도입 전부터 사용된 단일 사용자 식별자. 둘 다 보존해
    # 라우터/리포지토리가 점진적으로 owner_* 로 이전할 수 있게 한다.
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    original_filename: Mapped[str | None] = mapped_column(Text, nullable=True)
    stored_filename: Mapped[str | None] = mapped_column(Text, nullable=True)
    file_extension: Mapped[str | None] = mapped_column(Text, nullable=True)
    file_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    content_type: Mapped[str | None] = mapped_column(Text, nullable=True)


# =============================================================================
# 9.2.7 document_texts
# =============================================================================
class DocumentText(Base):
    __tablename__ = "document_texts"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    document_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("documents.id"), nullable=False
    )
    extracted_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    cleaned_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    keywords: Mapped[str | None] = mapped_column(Text, nullable=True)  # 콤마/JSON 문자열
    text_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_at: Mapped[str | None] = mapped_column(Text, nullable=True)


# =============================================================================
# 9.2.8 document_versions
# =============================================================================
# 기존 _init_db() 가 만든 컬럼 + 명세의 file_path 추가.
class DocumentVersion(Base):
    __tablename__ = "document_versions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    document_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("documents.id"), nullable=False
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    # 명세 추가 컬럼: 그 시점의 원본 파일 경로 (롤백/복원용).
    file_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    text_snapshot: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)


# =============================================================================
# 9.2.9 ocr_sources
# =============================================================================
class OcrSource(Base):
    __tablename__ = "ocr_sources"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    document_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("documents.id"), nullable=True
    )
    image_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    cleaned_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    # confidence 는 0.0 ~ 1.0 실수. SQLite 는 REAL 로 저장.
    confidence: Mapped[float | None] = mapped_column(nullable=True)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)


# =============================================================================
# 9.2.10 voice_commands
# =============================================================================
class VoiceCommand(Base):
    __tablename__ = "voice_commands"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    document_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("documents.id"), nullable=True
    )
    transcript: Mapped[str | None] = mapped_column(Text, nullable=True)
    # input_type: "text" | "audio".
    input_type: Mapped[str] = mapped_column(Text, nullable=False, default="text")
    audio_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    # status: "received" | "transcribed" | "failed".
    status: Mapped[str] = mapped_column(Text, nullable=False, default="received")
    created_at: Mapped[str] = mapped_column(Text, nullable=False)


# =============================================================================
# 9.2.11 edit_operations
# =============================================================================
class EditOperation(Base):
    __tablename__ = "edit_operations"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    document_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("documents.id"), nullable=False
    )
    # AI 명령과 매칭되는 voice_commands.id (또는 별도 명령 식별자).
    command_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("voice_commands.id"), nullable=True
    )
    # operation_type: "replace" | "insert" | "delete" 등. enum 강제는 추후.
    operation_type: Mapped[str] = mapped_column(Text, nullable=False)
    # target_json: 연산 대상의 위치/범위 정보 (JSON 문자열).
    target_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    before_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    after_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    # status: "pending" | "applied" | "rejected".
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    confidence: Mapped[float | None] = mapped_column(nullable=True)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)


# =============================================================================
# 9.2.12 extracted_fields
# =============================================================================
class ExtractedField(Base):
    __tablename__ = "extracted_fields"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    document_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("documents.id"), nullable=False
    )
    label: Mapped[str | None] = mapped_column(Text, nullable=True)
    # field_type: "text" | "date" | "number" | "select" 등.
    field_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    # AI 가 제안한 자동 채움 후보 값.
    suggestion: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[float | None] = mapped_column(nullable=True)
    # status: "pending" | "accepted" | "rejected" | "edited".
    status: Mapped[str | None] = mapped_column(Text, nullable=True)


# =============================================================================
# 9.2.13 answer_histories
# =============================================================================
class AnswerHistory(Base):
    __tablename__ = "answer_histories"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("demo_users.id"), nullable=True
    )
    document_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("documents.id"), nullable=True
    )
    question: Mapped[str | None] = mapped_column(Text, nullable=True)
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    category: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 콤마/JSON 문자열로 키워드 보관 (SQLite native array 미지원).
    keywords: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)


# =============================================================================
# 9.2.14 ai_recommendations
# =============================================================================
class AiRecommendation(Base):
    __tablename__ = "ai_recommendations"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    document_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("documents.id"), nullable=True
    )
    # source_ids: 참조한 다른 문서/필드 id 들의 JSON 배열 문자열.
    source_ids: Mapped[str | None] = mapped_column(Text, nullable=True)
    # prompt_type: "form_fill" | "summary" | "rewrite" 등.
    prompt_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)


# =============================================================================
# 9.2.15 external_connections
# =============================================================================
class ExternalConnection(Base):
    __tablename__ = "external_connections"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    # provider: "google_drive" | "notion" 등.
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    # status: "connected" | "disconnected" | "error" | "coming_soon".
    status: Mapped[str] = mapped_column(Text, nullable=False, default="disconnected")
    # 실제 토큰은 본 컬럼에 직접 저장하지 말고, 외부 secret store 의 참조 키만 저장.
    access_token_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)


# =============================================================================
# 9.2.16 external_documents
# =============================================================================
class ExternalDocument(Base):
    __tablename__ = "external_documents"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    # external_id: provider 측 시스템의 원본 ID (Drive file id 등).
    external_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    mime_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    # imported_document_id: documents.id 와 1:1 매핑. NULL 이면 미임포트 상태.
    imported_document_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("documents.id"), nullable=True
    )
    # raw_meta_json: provider 가 돌려준 원본 메타데이터 (디버깅/감사용).
    raw_meta_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)


# =============================================================================
# 외부 export 목록 (`from app.models import *` 사용 시).
# 명시적 __all__ 로 두어 기준이 분명하게 한다.
# =============================================================================
__all__ = [
    "Base",
    "DemoUser",
    "DemoProfile",
    "Folder",
    "Group",
    "GroupMember",
    "Document",
    "DocumentText",
    "DocumentVersion",
    "OcrSource",
    "VoiceCommand",
    "EditOperation",
    "ExtractedField",
    "AnswerHistory",
    "AiRecommendation",
    "ExternalConnection",
    "ExternalDocument",
]
