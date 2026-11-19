from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, backref, mapped_column, relationship

from app.core.db import Base

if TYPE_CHECKING:
    from app.sources.models import Source
    from app.users.models import User


class MaintenanceSession(Base):
    __tablename__ = "maintenance_sessions"

    session_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(
            "sources.source_id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    inspector_notes: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    is_synced: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default=text("false"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
    )
    deleted_by: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(
            "users.user_id",
            ondelete="SET NULL",
        ),
        nullable=True,
        default=None,
    )

    source: Mapped["Source"] = relationship(
        "Source",
        back_populates="maintenance_sessions",
        lazy="noload",
    )
    deleted_by_user: Mapped["User | None"] = relationship(
        "User",
        foreign_keys=[deleted_by],
        lazy="noload",
    )
    inspectors: Mapped[list["User"]] = relationship(
        "User",
        secondary="maintenance_inspectors",
        backref=backref("inspected_sessions", lazy="noload"),
        lazy="noload",
    )
    session_inspectors: Mapped[list["MaintenanceInspector"]] = relationship(
        "MaintenanceInspector",
        back_populates="session",
        cascade="all, delete-orphan",
        overlaps="inspectors,inspected_sessions",
        lazy="noload",
    )
    photos: Mapped[list["MaintenancePhoto"]] = relationship(
        "MaintenancePhoto",
        back_populates="session",
        cascade="all, delete-orphan",
        lazy="noload",
    )

    __table_args__ = (
        Index(
            "idx_maintenance_sessions_active_source_created_at",
            "source_id",
            text("created_at DESC"),
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "idx_maintenance_sessions_unsynced",
            "is_synced",
            postgresql_where=text("is_synced IS FALSE AND deleted_at IS NULL"),
        ),
    )

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None

    def __repr__(self) -> str:
        return (
            f"<MaintenanceSession(session_id={self.session_id!r}, "
            f"source_id={self.source_id!r}, "
            f"is_synced={self.is_synced!r}, "
            f"deleted_at={self.deleted_at!r})>"
        )


class MaintenanceInspector(Base):
    __tablename__ = "maintenance_inspectors"

    session_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(
            "maintenance_sessions.session_id",
            ondelete="CASCADE",
        ),
        primary_key=True,
        nullable=False,
    )
    inspector_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(
            "users.user_id",
            ondelete="CASCADE",
        ),
        primary_key=True,
        nullable=False,
    )

    session: Mapped["MaintenanceSession"] = relationship(
        "MaintenanceSession",
        back_populates="session_inspectors",
        overlaps="inspectors,inspected_sessions",
        lazy="noload",
    )
    inspector: Mapped["User"] = relationship(
        "User",
        overlaps="inspectors,inspected_sessions",
        lazy="noload",
    )

    __table_args__ = (
        Index(
            "idx_maintenance_inspectors_inspector_id",
            "inspector_id",
        ),
    )


class MaintenancePhoto(Base):
    __tablename__ = "maintenance_photos"

    photo_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(
            "maintenance_sessions.session_id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    photo_url: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    uploaded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
    )
    deleted_by: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(
            "users.user_id",
            ondelete="SET NULL",
        ),
        nullable=True,
        default=None,
    )

    session: Mapped["MaintenanceSession"] = relationship(
        "MaintenanceSession",
        back_populates="photos",
        lazy="noload",
    )
    deleted_by_user: Mapped["User | None"] = relationship(
        "User",
        foreign_keys=[deleted_by],
        lazy="noload",
    )

    __table_args__ = (
        Index(
            "idx_maintenance_photos_active_session_created_at",
            "session_id",
            "created_at",
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None

    def __repr__(self) -> str:
        return (
            f"<MaintenancePhoto(photo_id={self.photo_id!r}, "
            f"session_id={self.session_id!r}, "
            f"deleted_at={self.deleted_at!r})>"
        )
