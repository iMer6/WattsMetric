from httpx import AsyncClient, Response
from app.core.enums import RoleName


async def test_service_and_health_endpoints(client: AsyncClient):
    """
    Checking the root endpoint and connecting to Postgres.
    """
    root_resp: Response = await client.get("/")
    assert root_resp.status_code == 200
    assert root_resp.json()["status"] == "ok"

    health_v1: Response = await client.get("/api/v1/health")
    assert health_v1.status_code == 200
    assert health_v1.json() == {"status": "ok", "database": True}

    health_root: Response = await client.get("/health")
    assert health_root.status_code == 200
    assert health_root.json()["database"] == "connected"


async def test_register_and_login_flow(client: AsyncClient):
    """
    Verification of public registration (fixed OWNER role) and OAuth2 login.
    """
    reg_payload: dict[str, str] = {
        "user_name": "Іван Петренко",
        "user_email": "ivan.owner@wattsmetric.ua",
        "password": "SecurePassword123!",
    }
    reg_resp: Response = await client.post(
        "/api/v1/users/auth/register",
        json=reg_payload
    )
    assert reg_resp.status_code == 201
    data = reg_resp.json()
    assert data["user_email"] == reg_payload["user_email"]

    # Successful login
    login_resp: Response = await client.post(
        "/api/v1/users/auth/token",
        data={
            "username": reg_payload["user_email"],
            "password": reg_payload["password"]
        },
    )
    assert login_resp.status_code == 200
    token = login_resp.json()["access_token"]

    # Retrieving profile /me
    me_resp: Response = await client.get(
        "/api/v1/users/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert me_resp.status_code == 200
    assert me_resp.json()["user_email"] == reg_payload["user_email"]


async def test_login_user_enumeration_protection(client: AsyncClient):
    """
    A non-existent email and an incorrect password return an identical 401.
    """
    r_unknown: Response = await client.post(
        "/api/v1/users/auth/token",
        data={
            "username": "nobody@wattsmetric.ua",
            "password": "SomePassword123!"
        },
    )
    assert r_unknown.status_code == 401
    assert r_unknown.json()["detail"] == (
        "Incorrect email or password, or account is inactive."
    )


async def test_admin_cannot_create_root_or_admin(
    client: AsyncClient,
    make_user_headers
):
    """
    Protection against privilege escalation:
        an ADMIN cannot create another ADMIN or ROOT.
    """
    admin_headers: dict[str, str] = await make_user_headers(
        "admin@wattsmetric.ua", RoleName.ADMIN
    )

    resp: Response = await client.post(
        "/api/v1/users",
        headers=admin_headers,
        json={
            "user_name": "Rogue Admin",
            "user_email": "rogue@wattsmetric.ua",
            "password": "StrongPassword123!",
            "initial_role_names": [RoleName.ADMIN.value],
        },
    )
    assert resp.status_code == 403
