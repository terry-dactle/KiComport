from __future__ import annotations

import enum
from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, Column, DateTime, Enum, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class JobStatus(str, enum.Enum):
    pending = "pending"
    analyzing = "analyzing"
    waiting_for_user = "waiting_for_user"
    waiting_for_import = "waiting_for_import"
    imported = "imported"
    duplicate = "duplicate"
    error = "error"


class CandidateType(str, enum.Enum):
    symbol = "symbol"
    footprint = "footprint"
    model = "model"


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    md5: Mapped[str] = mapped_column(String(64), index=True)
    original_filename: Mapped[str] = mapped_column(String(255))
    stored_path: Mapped[str] = mapped_column(Text)
    extracted_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[JobStatus] = mapped_column(Enum(JobStatus), default=JobStatus.pending, index=True)
    is_duplicate: Mapped[bool] = mapped_column(default=False)
    ai_failed: Mapped[bool] = mapped_column(default=False)
    message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    components: Mapped[list["Component"]] = relationship("Component", back_populates="job", cascade="all, delete-orphan")
    logs: Mapped[list["JobLog"]] = relationship("JobLog", back_populates="job", cascade="all, delete-orphan")

    __table_args__ = (UniqueConstraint("md5", "original_filename", name="uq_job_md5_filename"),)


class Component(Base):
    __tablename__ = "components"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    selected_symbol_id: Mapped[Optional[int]] = mapped_column(ForeignKey("candidate_files.id"), nullable=True)
    selected_footprint_id: Mapped[Optional[int]] = mapped_column(ForeignKey("candidate_files.id"), nullable=True)
    selected_model_id: Mapped[Optional[int]] = mapped_column(ForeignKey("candidate_files.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    job: Mapped[Job] = relationship("Job", back_populates="components")
    candidates: Mapped[list["CandidateFile"]] = relationship(
        "CandidateFile",
        back_populates="component",
        cascade="all, delete-orphan",
        foreign_keys="CandidateFile.component_id",
    )


class CandidateFile(Base):
    __tablename__ = "candidate_files"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    component_id: Mapped[int] = mapped_column(ForeignKey("components.id"), index=True)
    type: Mapped[CandidateType] = mapped_column(Enum(CandidateType))
    path: Mapped[str] = mapped_column(Text)
    rel_path: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    pin_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    pad_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    heuristic_score: Mapped[float] = mapped_column(default=0.0)
    ai_score: Mapped[Optional[float]] = mapped_column(default=None)
    combined_score: Mapped[float] = mapped_column(default=0.0)
    quality_score: Mapped[float] = mapped_column(default=0.0)
    feedback_score: Mapped[float] = mapped_column(default=0.0)
    selected_count: Mapped[int] = mapped_column(Integer, default=0)
    ai_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    component: Mapped[Component] = relationship(
        "Component",
        back_populates="candidates",
        foreign_keys=[component_id],
    )

class JobLog(Base):
    __tablename__ = "job_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), index=True)
    level: Mapped[str] = mapped_column(String(16), default="INFO")
    message: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    job: Mapped[Job] = relationship("Job", back_populates="logs")
