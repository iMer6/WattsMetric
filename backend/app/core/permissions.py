"""
Access permissions matrix by role.
"""

from __future__ import annotations

from app.core.enums import RoleName

ALL_ROLES: frozenset[RoleName] = frozenset(role for role in RoleName)

# Roles assignable at the global system level
GLOBAL_ROLES: frozenset[RoleName] = frozenset({
    RoleName.ROOT,
    RoleName.ADMIN,
})

# Management roles: full access to directories and users
ADMIN_ROLES: frozenset[RoleName] = frozenset({
    RoleName.ROOT,
    RoleName.ADMIN,
})

# Roles assignable per specific energy source
SOURCE_ROLES: frozenset[RoleName] = frozenset({
    RoleName.OWNER,
    RoleName.DISPATCHER,
    RoleName.ENGINEER,
})

# User accounts management -- root and admin only
USER_MANAGE_ROLES: frozenset[RoleName] = ADMIN_ROLES

# Viewing source dashboard, telemetry and energy aggregates
SOURCE_READ_ROLES: frozenset[RoleName] = ADMIN_ROLES | SOURCE_ROLES

# Energy sources creating, editing and status management
SOURCE_WRITE_ROLES: frozenset[RoleName] = ADMIN_ROLES | {RoleName.OWNER}

# Deactivating or archiving an energy source
SOURCE_DELETE_ROLES: frozenset[RoleName] = ADMIN_ROLES | {RoleName.OWNER}

# Managing user_source_access (granting/revoking access to a specific source)
SOURCE_ACCESS_MANAGE_ROLES: frozenset[RoleName] = ADMIN_ROLES | {RoleName.OWNER}

# Viewing IoT device status, firmware version and sensor config
IOT_DEVICE_READ_ROLES: frozenset[RoleName] = ADMIN_ROLES | SOURCE_ROLES

# Commissioning, configuring and deactivating IoT devices (ESP32, BLE provisioning)
IOT_DEVICE_MANAGE_ROLES: frozenset[RoleName] = frozenset({
    RoleName.OWNER,
    RoleName.ENGINEER,
})

# Viewing maintenance history and inspector notes
MAINTENANCE_READ_ROLES: frozenset[RoleName] = ADMIN_ROLES | SOURCE_ROLES

# Creating and syncing field maintenance sessions & photos
MAINTENANCE_WRITE_ROLES: frozenset[RoleName] = frozenset({RoleName.ENGINEER})

# Configuring generation anomaly alert rules and thresholds
ALERT_RULES_MANAGE_ROLES: frozenset[RoleName] = ADMIN_ROLES | {
    RoleName.OWNER,
    RoleName.DISPATCHER,
}

# Triggering local LLM explanations for generation drops
LLM_ANALYSE_ROLES: frozenset[RoleName] = ADMIN_ROLES | {
    RoleName.OWNER,
    RoleName.DISPATCHER,
}

# Managing and replaying test profiles
TEST_PROFILE_MANAGE_ROLES: frozenset[RoleName] = ADMIN_ROLES | {
    RoleName.OWNER,
    RoleName.DISPATCHER
}


def permissions_for(role_name: RoleName | str) -> dict[str, bool]:
    """
    Return a dictionary of capabilities for a role (for the UI).
    
    Supports both a RoleName object and a string (for DB compatibility).
    """
    try:
        role_enum: RoleName = RoleName(role_name)
    except ValueError:
        return {
            "users_manage": False,
            "source_read": False,
            "source_write": False,
            "source_delete": False,
            "source_access_manage": False,
            "iot_devices_read": False,
            "iot_devices_manage": False,
            "maintenance_read": False,
            "maintenance_write": False,
            "alert_rules_manage": False,
            "llm_analyze": False,
            "test_profiles_manage": False,
        }

    return {
        "users_manage": role_enum in USER_MANAGE_ROLES,
        "source_read": role_enum in SOURCE_READ_ROLES,
        "source_write": role_enum in SOURCE_WRITE_ROLES,
        "source_delete": role_enum in SOURCE_DELETE_ROLES,
        "source_access_manage": role_enum in SOURCE_ACCESS_MANAGE_ROLES,
        "iot_devices_read": role_enum in IOT_DEVICE_READ_ROLES,
        "iot_devices_manage": role_enum in IOT_DEVICE_MANAGE_ROLES,
        "maintenance_read": role_enum in MAINTENANCE_READ_ROLES,
        "maintenance_write": role_enum in MAINTENANCE_WRITE_ROLES,
        "alert_rules_manage": role_enum in ALERT_RULES_MANAGE_ROLES,
        "llm_analyse": role_enum in LLM_ANALYSE_ROLES,
        "test_profiles_manage": role_enum in TEST_PROFILE_MANAGE_ROLES,
    }


def merged_permissions_for(roles: list[RoleName | str]) -> dict[str, bool]:
    """
    Merge capabilities across multiple roles assigned to a user (logical OR).
    Useful for checking combined source-level access.
    """
    result: dict[str, bool] = permissions_for("__unknown__")
    for role in roles:
        for capability, is_allowed in permissions_for(role).items():
            result[capability] = result[capability] or is_allowed
    
    return result
