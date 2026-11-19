from enum import Enum


class RoleName(str, Enum):
    ROOT = "root"
    ADMIN = "administrator"
    OWNER = "owner"
    ENGINEER = "engineer"
    DISPATCHER = "dispatcher"


class Permission(str, Enum):
    # Monitoring and calculations
    
    VIEW_SOURCES = "sources:view"  # source panel, sensors status, telemetry archive
    VIEW_ANALYTICS = "analytics:view"
    VERIFY_CALCULATIONS = "calculations:verify"
    
    # Shortfalls/changes notifications and AI-diagnostics
    
    VIEW_ALERTS = "alerts:view"
    MANAGE_ALERTS_RULES = "alert_rules:manage"
    USE_LLM_DIAGNOSTICS = "llm_diagnostics:use"
    REPLAY_TEST_PROFILES = "test_profiles:replay"
    
    # Service and IoT
    
    VIEW_MAINTENANCE_LOGS = "maintenance:view"
    CREATE_MAINTENANCE_LOGS = "maintenance:create"
    COMMISSION_IOT_NODES = "iot_nodes:commission"
    MANAGE_IOT_DEVICES = "iot_devices:manage"
    
    # Reporting
    
    VIEW_REPORTS = "reports:view"
    CREATE_REPORTS = "reports:create"
    
    # Administration
    
    VIEW_USERS = "users:view"
    CREATE_USERS = "users:create"
    MANAGE_USERS = "users:manage"
    MANAGE_ROLES = "roles:manage"


class SourceStatus(str, Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    MAINTENANCE = "maintenance"
    OFFLINE = "offline"
