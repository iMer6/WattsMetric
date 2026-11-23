from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Index,
    Numeric,
    String,
    func,
    text, Boolean,
)
from sqlalchemy.dialects.postgresql import ENUM as PG_ENUM
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, backref, mapped_column, relationship

from app.core.db import Base
from app.core.enums import SourceStatus

if TYPE_CHECKING:
    from app.iot.models import IoTDevice
    from app.maintenance.models import MaintenanceSession
    from app.users.models import UserSourceAccess


class Source(Base):
    __tablename__ = "sources"

    source_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    source_name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )
    source_address: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    latitude: Mapped[Decimal | None] = mapped_column(
        Numeric(9, 6),
        nullable=True,
    )
    longitude: Mapped[Decimal | None] = mapped_column(
        Numeric(9, 6),
        nullable=True,
    )
    qr_code: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        nullable=False,
    )
    source_status: Mapped[SourceStatus] = mapped_column(
        PG_ENUM(
            SourceStatus,
            name="source_status_enum",
            create_type=True,
            values_callable=lambda enum_cls: [
                member.value for member in enum_cls
            ],
        ),
        nullable=False,
        default=SourceStatus.ACTIVE,
        server_default=text(f"'{SourceStatus.ACTIVE.value}'"),
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    iot_devices: Mapped[list["IoTDevice"]] = relationship(
        "IoTDevice",
        back_populates="source",
        cascade="all, delete-orphan",
        lazy="raise",
    )
    maintenance_sessions: Mapped[list["MaintenanceSession"]] = relationship(
        "MaintenanceSession",
        back_populates="source",
        cascade="all, delete-orphan",
        lazy="raise",
    )
    user_accesses: Mapped[list["UserSourceAccess"]] = relationship(
        "UserSourceAccess",
        backref=backref("source", lazy="raise"),
        cascade="all, delete-orphan",
        lazy="raise",
    )

    __table_args__ = (
        Index(
            "idx_sources_qr_code",
            "qr_code",
            unique=True
        ),
        Index(
            "idx_sources_active_status_created_at",
            "is_active", "source_status", "created_at"
        ),
        CheckConstraint(
            "latitude IS NULL OR (latitude >= -90 AND latitude <= 90)",
            name="ck_sources_latitude_range",
        ),
        CheckConstraint(
            "longitude IS NULL OR (longitude >= -180 AND longitude <= 180)",
            name="ck_sources_longitude_range",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<Source(source_id={self.source_id!r}, "
            f"source_name={self.source_name!r}, "
            f"status={self.source_status!r}"
            f"is_active={self.is_active!r})>"
        )
