from enum import Enum

class RoleName(str, Enum):
    ROOT = "root"
    ADMIN = "administrator"
    OWNER = "owner"
    ENGINEER = "engineer"
    DISPATCHER = "dispatcher"
    GUEST = "guest"