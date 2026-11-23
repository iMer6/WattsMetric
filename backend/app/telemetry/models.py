from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base

if TYPE_CHECKING:
    from app.iot.models import IoTDevice


class Measurement(Base):
    __tablename__ = "measurements"

    iot_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(
            "iot_devices.iot_id",
            ondelete="CASCADE",
        ),
        primary_key=True,
        nullable=False,
    )
    measured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        primary_key=True,
        nullable=False,
    )
    voltage: Mapped[Decimal | None] = mapped_column(
        Numeric(6, 2),
        nullable=True,
    )
    current: Mapped[Decimal | None] = mapped_column(
        Numeric(6, 2),
        nullable=True,
    )
    temperature: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 2),
        nullable=True,
    )
    illumination: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    is_gap: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default=text("false"),
        nullable=False,
    )

    iot_device: Mapped["IoTDevice"] = relationship(
        "IoTDevice",
        back_populates="measurements",
        lazy="raise",
    )

    __table_args__ = (
        Index(
            "idx_measurements_iot_id_measured_at_gap",
            "iot_id", "measured_at",
            postgresql_where=text("is_gap IS TRUE"),
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<Measurement(iot_id={self.iot_id!r}, "
            f"measured_at={self.measured_at!r}, "
            f"is_gap={self.is_gap!r})>"
        )


class TimeAggregate(Base):
    __tablename__ = "time_aggregates"

    iot_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(
            "iot_devices.iot_id",
            ondelete="CASCADE",
        ),
        primary_key=True,
        nullable=False,
    )
    bucket: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        primary_key=True,
        nullable=False,
    )
    total_energy: Mapped[Decimal] = mapped_column(
        Numeric(10, 3),
        nullable=False,
    )
    avg_voltage: Mapped[Decimal | None] = mapped_column(
        Numeric(6, 2),
        nullable=True,
    )
    avg_current: Mapped[Decimal | None] = mapped_column(
        Numeric(6, 2),
        nullable=True,
    )
    avg_temperature: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 2),
        nullable=True,
    )
    avg_illumination: Mapped[Decimal | None] = mapped_column(
        Numeric(7, 2),
        nullable=True,
    )

    iot_device: Mapped["IoTDevice"] = relationship(
        "IoTDevice",
        back_populates="time_aggregates",
        lazy="raise",
    )

    def __repr__(self) -> str:
        return (
            f"<TimeAggregate(iot_id={self.iot_id!r}, "
            f"bucket={self.bucket!r}, "
            f"total_energy={self.total_energy!r})>"
        )


class TestProfile(Base):
    __tablename__ = "test_profiles"

    test_profile_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    test_profile_name: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        nullable=False,
    )
    profile_payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        Index(
            "idx_test_profiles_name",
            "test_profile_name",
            unique=True
        ),
        Index(
            "idx_test_profiles_created_at",
            "created_at"
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<TestProfile(test_profile_id={self.test_profile_id!r}, "
            f"test_profile_name={self.test_profile_name!r})>"
        )
