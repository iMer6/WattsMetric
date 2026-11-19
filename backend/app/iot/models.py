from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, MACADDR
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base

if TYPE_CHECKING:
    from app.sources.models import Source
    from app.telemetry.models import Measurement, TimeAggregate


class IoTDevice(Base):
    __tablename__ = "iot_devices"

    iot_id: Mapped[uuid.UUID] = mapped_column(
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
    esp32_mac: Mapped[str] = mapped_column(
        MACADDR,
        unique=True,
        nullable=False,
    )
    firmware_version: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )
    sensor_config: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        nullable=True,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        server_default=text("true"),
        nullable=False,
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    source: Mapped["Source"] = relationship(
        "Source",
        back_populates="iot_devices",
        lazy="noload",
    )
    measurements: Mapped[list["Measurement"]] = relationship(
        "Measurement",
        back_populates="iot_device",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="noload",
    )
    time_aggregates: Mapped[list["TimeAggregate"]] = relationship(
        "TimeAggregate",
        back_populates="iot_device",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="noload",
    )

    __table_args__ = (
        Index(
            "idx_iot_devices_esp32_mac",
            "esp32_mac",
            unique=True
        ),
        Index(
            "idx_iot_devices_source_id_is_active",
            "source_id", "is_active"
        ),
        Index(
            "idx_iot_devices_last_seen_at",
            "last_seen_at"
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<IoTDevice(iot_id={self.iot_id!r}, "
            f"esp32_mac={self.esp32_mac!r}, "
            f"is_active={self.is_active!r})>"
        )
