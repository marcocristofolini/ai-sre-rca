from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, JSON, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class InvestigationRecord(Base):
    __tablename__ = "investigations"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: f"inc-{uuid4().hex[:12]}")
    status: Mapped[str] = mapped_column(String(32), index=True)
    title: Mapped[str] = mapped_column(Text)
    alert_name: Mapped[str] = mapped_column(String(255), index=True)
    cluster: Mapped[str] = mapped_column(String(255), index=True)
    namespace: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    pod: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    root_cause: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
