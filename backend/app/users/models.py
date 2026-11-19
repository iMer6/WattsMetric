from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Boolean,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base

if TYPE_CHECKING:
    pass


class User(Base):
    __tablename__ = "users"
    
    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    user_name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )
    user_email: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    password_hash: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        repr=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        server_default=text("true"),
        nullable=False,
    )
    
    roles: Mapped[list["Role"]] = relationship(
        "Role",
        secondary="user_roles",
        back_populates="users",
        lazy="selectin",
    )
    user_roles: Mapped[list["UserRole"]] = relationship(
        "UserRole",
        back_populates="user",
        cascade="all, delete-orphan",
        overlaps="roles",
        lazy="noload",
    )
    source_accesses: Mapped[list["UserSourceAccess"]] = relationship(
        "UserSourceAccess",
        back_populates="user",
        cascade="all, delete-orphan",
        lazy="noload",
    )
    
    @property
    def has_roles(self) -> bool:
        """
        Is at least one role assigned to the user?
        """
        return bool(self.roles)
    
    @property
    def can_access(self) -> bool:
        """
        A user can work in the system
        only if the account is active AND has roles.
        """
        return self.is_active and self.has_roles
    
    __table_args__ = (
        Index(
            "idx_users_user_email_lower",
            func.lower(text("user_email")),
            unique=True,
        ),
    )


class Role(Base):
    __tablename__ = "roles"
    
    role_id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )
    role_name: Mapped[str] = mapped_column(
        String(100),
        unique=True,
        nullable=False,
    )
    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    
    users: Mapped[list["User"]] = relationship(
        "User",
        secondary="user_roles",
        back_populates="roles",
        overlaps="user_roles",
        lazy="noload",
    )
    source_accesses: Mapped[list["UserSourceAccess"]] = relationship(
        "UserSourceAccess",
        back_populates="role",
        lazy="noload",
    )


class UserRole(Base):
    __tablename__ = "user_roles"
    
    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(
            "users.user_id",
            ondelete="CASCADE",
        ),
        primary_key=True,
        nullable=False,
    )
    role_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey(
            "roles.role_id",
            ondelete="CASCADE"
        ),
        primary_key=True,
        nullable=False,
    )
    
    user: Mapped["User"] = relationship(
        "User",
        back_populates="user_roles",
        overlaps="roles,users",
        lazy="noload",
    )
    role: Mapped["Role"] = relationship(
        "Role",
        overlaps="roles,users",
        lazy="selectin",
    )
    
    __table_args__ = (
        Index(
            "idx_user_roles_role_id",
            "role_id"
        ),
    )


class UserSourceAccess(Base):
    __tablename__ = "user_source_access"
    
    access_id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(
            "users.user_id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(
            "sources.source_id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    role_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey(
            "roles.role_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    
    user: Mapped["User"] = relationship(
        "User",
        back_populates="source_accesses",
        lazy="selectin",
    )
    role: Mapped["Role"] = relationship(
        "Role",
        back_populates="source_accesses",
        lazy="selectin",
    )
    
    __table_args__ = (
        UniqueConstraint(
            "user_id", "source_id",
            name="uq_user_source_access_user_source",
        ),
        Index(
            "idx_user_source_access_source_id_role_id",
            "source_id", "role_id"
        ),
        Index(
            "idx_user_source_access_user_id",
            "user_id"
        ),
    )
