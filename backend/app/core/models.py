"""
Centralized import of all SQLAlchemy ORM models so that Base.metadata
and relationship() mappers resolve property regardless of active routers.
"""

import app.users.models  # noqa: F401
import app.sources.models  # noqa: F401
import app.iot.models  # noqa: F401
import app.telemetry.models  # noqa: F401
import app.maintenance.models  # noqa: F401
