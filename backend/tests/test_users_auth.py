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


async def test_user_profile_and_password_change(
    client: AsyncClient,
    make_user_headers
):
    """
    Updating your profile (/me) and changing your password.
    """
    email: str = "profile.user@wattsmetric.ua"
    old_pass: str = "OldPassword123!"
    new_pass: str = "NewStrongPassword456!"
    headers: dict[str, str] = await make_user_headers(
        email, RoleName.OWNER, password=old_pass
    )

    # PATCH /me
    patch_resp: Response = await client.patch(
        "/api/v1/users/me",
        headers=headers,
        json={"user_name": "Оновлене Ім'я"},
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["user_name"] == "Оновлене Ім'я"

    # POST /me/password
    pwd_resp: Response = await client.post(
        "/api/v1/users/me/password",
        headers=headers,
        json={
            "current_password": old_pass,
            "new_password": new_pass
        },
    )
    assert pwd_resp.status_code == 204

    # The old password no longer works; the new one works
    fail_login: Response = await client.post(
        "/api/v1/users/auth/token",
        data={
            "username": email,
            "password": old_pass
        },
    )
    assert fail_login.status_code == 401

    ok_login: Response = await client.post(
        "/api/v1/users/auth/token",
        data={
            "username": email,
            "password": new_pass
        },
    )
    assert ok_login.status_code == 200


async def test_root_user_administration_and_roles(
    client: AsyncClient,
    make_user_headers
):
    """
    Full-cycle administration of users and system roles (ROOT).
    """
    root_headers: dict[str, str] = await make_user_headers(
        "root@wattsmetric.ua", RoleName.ROOT
    )

    # Retrieve the list of roles
    roles = (
        await client.get(
            "/api/v1/users/roles",
            headers=root_headers
        )
    ).json()
    roles_by_name = {
        role["role_name"]: role["role_id"] for role in roles
    }

    # POST /users -- ROOT creates ADMIN
    create_resp: Response = await client.post(
        "/api/v1/users",
        headers=root_headers,
        json={
            "user_name": "New Admin",
            "user_email": "managed.admin@wattsmetric.ua",
            "password": "AdminPassword123!",
            "initial_role_names": [RoleName.ADMIN.value],
        },
    )
    assert create_resp.status_code == 201
    admin_id = create_resp.json()["user_id"]

    # GET /users (pagination and search)
    list_resp: Response = await client.get(
        "/api/v1/users",
        headers=root_headers,
        params={
            "search": "managed.admin",
            "is_active": True
        },
    )
    assert list_resp.status_code == 200
    assert list_resp.json()["total"] >= 1
    assert "X-Total-Count" in list_resp.headers

    # GET /users/{user_id} and PATCH /users/{user_id}
    detail_resp: Response = await client.get(
        f"/api/v1/users/{admin_id}",
        headers=root_headers,
        params={"include_accesses": True},
    )
    assert detail_resp.status_code == 200

    upd_resp: Response = await client.patch(
        f"/api/v1/users/{admin_id}",
        headers=root_headers,
        json={"user_name": "Updated Admin"},
    )
    assert upd_resp.status_code == 200
    assert upd_resp.json()["user_name"] == "Updated Admin"

    # POST and DELETE /users/{user_id}/roles (non-system role)
    disp_role_id = roles_by_name[RoleName.DISPATCHER.value]
    add_role: Response = await client.post(
        f"/api/v1/users/{admin_id}/roles",
        headers=root_headers,
        json={"role_id": disp_role_id},
    )
    assert add_role.status_code == 200

    del_role: Response = await client.delete(
        f"/api/v1/users/{admin_id}/roles/{disp_role_id}",
        headers=root_headers,
    )
    assert del_role.status_code == 200

    # Protection of system roles: revoking ADMIN role or deactivating ADMIN ==> 422
    admin_role_id = roles_by_name[RoleName.ADMIN.value]
    revoke_admin: Response = await client.delete(
        f"/api/v1/users/{admin_id}/roles/{admin_role_id}",
        headers=root_headers,
    )
    assert revoke_admin.status_code == 422

    blocked_deactivation: Response = await client.delete(
        f"/api/v1/users/{admin_id}",
        headers=root_headers,
    )
    assert blocked_deactivation.status_code == 422

    # Create a regular OWNER user and verify deactivation ==> 200
    owner_resp: Response = await client.post(
        "/api/v1/users",
        headers=root_headers,
        json={
            "user_name": "Regular Owner",
            "user_email": "deact.owner@wattsmetric.ua",
            "password": "OwnerPassword123!",
            "initial_role_names": [RoleName.OWNER.value],
        },
    )
    assert owner_resp.status_code == 201
    owner_id = owner_resp.json()["user_id"]

    deactivation_resp: Response = await client.delete(
        f"/api/v1/users/{owner_id}",
        headers=root_headers,
    )
    assert deactivation_resp.status_code == 200
    assert deactivation_resp.json()["is_active"] is False

    # Deactivated user gets 401 when attempting to log in
    login_deactivation: Response = await client.post(
        "/api/v1/users/auth/token",
        data={
            "username": "deact.owner@wattsmetric.ua",
            "password": "OwnerPassword123!",
        },
    )
    assert login_deactivation.status_code == 401
