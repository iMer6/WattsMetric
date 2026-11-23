from httpx import AsyncClient, Response
from app.core.enums import RoleName


async def test_sources_lifecycle_and_object_access(
    client: AsyncClient,
    make_user_headers
):
    """
    Creation of a solar power plant (SPP), QR uniqueness check,
    visibility restricted to the owner, and soft-delete.
    """
    owner1_headers: dict[str, str] = await make_user_headers(
        "owner1@wattsmetric.ua", RoleName.OWNER
    )
    owner2_headers: dict[str, str] = await make_user_headers(
        "owner2@wattsmetric.ua", RoleName.OWNER
    )

    # Owner 1 creates sources
    create_resp: Response = await client.post(
        "/api/v1/sources",
        headers=owner1_headers,
        json={
            "source_name": "СЕС Південна-1",
            "qr_code": "WM-QR-2026-001",
            "source_status": "active",
            "source_address": "Харківська обл.",
            "latitude": 49.9935,
            "longitude": 36.2304,
        },
    )
    assert create_resp.status_code == 201
    source_id = create_resp.json()["source_id"]

    # Duplicate QR code returns 409 Conflict
    dup_resp: Response = await client.post(
        "/api/v1/sources",
        headers=owner1_headers,
        json={
            "source_name": "Дублікат СЕС",
            "qr_code": "WM-QR-2026-001",
            "source_status": "active",
        },
    )
    assert dup_resp.status_code == 409

    # Owner 2 does not see Owner 1's station.
    list_owner2: Response = await client.get(
        "/api/v1/sources", headers=owner2_headers
    )
    assert list_owner2.status_code == 200
    assert list_owner2.json()["total"] == 0

    # QR code search for Owner 1 works
    qr_resp: Response = await client.get(
        "/api/v1/sources/by-qr/WM-QR-2026-001", headers=owner1_headers
    )
    assert qr_resp.status_code == 200
    assert qr_resp.json()["source_id"] == source_id

    # Soft-delete (archiving)
    del_resp: Response = await client.delete(
        f"/api/v1/sources/{source_id}", headers=owner1_headers
    )
    assert del_resp.status_code == 204

    list_owner1_after: Response = await client.get(
        "/api/v1/sources", headers=owner1_headers
    )
    assert list_owner1_after.json()["total"] == 0


async def test_iot_devices_mac_uniqueness_and_isolation(
    client: AsyncClient,
    make_user_headers
):
    """
    ESP32 registration, MAC address conflict (409), and station isolation.
    """
    owner_headers: dict[str, str] = await make_user_headers(
        "iot_owner@wattsmetric.ua", RoleName.OWNER
    )

    s1 = (
        await client.post(
            "/api/v1/sources",
            headers=owner_headers,
            json={
                "source_name": "Source A",
                "qr_code": "QR-A-101",
                "source_status": "active"
            },
        )
    ).json()["source_id"]

    s2 = (
        await client.post(
            "/api/v1/sources",
            headers=owner_headers,
            json={
                "source_name": "Source B",
                "qr_code": "QR-B-102",
                "source_status": "active"
            },
        )
    ).json()["source_id"]

    # Registering ESP32 on Source A
    dev_resp: Response = await client.post(
        f"/api/v1/iot/{s1}/devices",
        headers=owner_headers,
        json={
            "esp32_mac": "24:6F:28:AA:BB:CC",
            "firmware_version": "2.1.0",
            "sensor_config": {"shunt_ohms": 0.01},
            "is_active": True,
        },
    )
    assert dev_resp.status_code == 201
    iot_id = dev_resp.json()["iot_id"]

    # Attempt to register the same MAC on Source B ==> 409
    conflict_resp: Response = await client.post(
        f"/api/v1/iot/{s2}/devices",
        headers=owner_headers,
        json={
            "esp32_mac": "24:6F:28:AA:BB:CC",
            "firmware_version": "2.1.0",
            "sensor_config": {},
            "is_active": True,
        },
    )
    assert conflict_resp.status_code == 409

    # Attempt to access Source A's device via Source B's URL ==> 404
    wrong_source_resp: Response = await client.get(
        f"/api/v1/iot/{s2}/devices/{iot_id}",
        headers=owner_headers,
    )
    assert wrong_source_resp.status_code == 404


async def test_source_update_access_management_and_iot_crud(
    client: AsyncClient,
    make_user_headers,
):
    """
    Source updates, site access rights management, and CRUD for ESP32.
    """
    owner_headers: dict[str, str] = await make_user_headers(
        "full_owner@wattsmetric.ua", RoleName.OWNER
    )
    staff_headers: dict[str, str] = await make_user_headers(
        "staff_user@wattsmetric.ua", RoleName.ENGINEER
    )

    staff_id = (
        await client.get(
            "/api/v1/users/me",
            headers=staff_headers
        )
    ).json()["user_id"]
    roles = (
        await client.get(
            "/api/v1/users/roles",
            headers=owner_headers
        )
    ).json()
    roles_map = {
        role["role_name"]: role["role_id"] for role in roles
    }

    # Creating a source
    source_id = (
        await client.post(
            "/api/v1/sources",
            headers=owner_headers,
            json={
                "source_name": "WPP Steppe",
                "qr_code": "WM-WIND-01",
                "source_status": "active"
            },
        )
    ).json()["source_id"]

    # GET /sources/{source_id} and PATCH /sources/{source_id}
    get_src: Response = await client.get(
        f"/api/v1/sources/{source_id}",
        headers=owner_headers
    )
    assert get_src.status_code == 200

    patch_src: Response = await client.patch(
        f"/api/v1/sources/{source_id}",
        headers=owner_headers,
        json={
            "source_name": "WPP Steppe modern",
            "source_status": "maintenance"
        },
    )
    assert patch_src.status_code == 200
    assert patch_src.json()["source_name"] == "WPP Steppe modern"
    assert patch_src.json()["source_status"] == "maintenance"

    # Object access: POST ==> GET ==> PATCH ==> DELETE
    grant: Response = await client.post(
        f"/api/v1/users/sources/{source_id}/accesses",
        headers=owner_headers,
        json={
            "user_id": staff_id,
            "role_id": roles_map[RoleName.ENGINEER.value]
        },
    )
    assert grant.status_code == 201

    access_list: Response = await client.get(
        f"/api/v1/users/sources/{source_id}/accesses",
        headers=owner_headers,
    )
    assert access_list.status_code == 200
    assert len(access_list.json()) >= 2  # OWNER + ENGINEER

    upd_access: Response = await client.patch(
        f"/api/v1/users/sources/{source_id}/accesses/{staff_id}",
        headers=owner_headers,
        json={
            "role_id": roles_map[RoleName.DISPATCHER.value]
        },
    )
    assert upd_access.status_code == 200

    # Full CRUD for ESP32: POST ==> GET list ==> GET detail ==> PATCH ==> DELETE
    iot_id = (
        await client.post(
            f"/api/v1/iot/{source_id}/devices",
            headers=owner_headers,
            json={
                "esp32_mac": "AA:BB:CC:DD:EE:01",
                "firmware_version": "1.0.0",
                "sensor_config": {"gain": 1},
                "is_active": True,
            },
        )
    ).json()["iot_id"]

    dev_list: Response = await client.get(
        f"/api/v1/iot/{source_id}/devices",
        headers=owner_headers
    )
    assert dev_list.status_code == 200
    assert len(dev_list.json()) == 1

    dev_detail: Response = await client.get(
        f"/api/v1/iot/{source_id}/devices/{iot_id}",
        headers=owner_headers,
    )
    assert dev_detail.status_code == 200
    assert dev_detail.json()["esp32_mac"] == "aa:bb:cc:dd:ee:01"

    dev_patch: Response = await client.patch(
        f"/api/v1/iot/{source_id}/devices/{iot_id}",
        headers=owner_headers,
        json={
            "firmware_version": "1.2.5",
            "sensor_config": {"gain": 2}
        },
    )
    assert dev_patch.status_code == 200
    assert dev_patch.json()["firmware_version"] == "1.2.5"

    dev_del: Response = await client.delete(
        f"/api/v1/iot/{source_id}/devices/{iot_id}",
        headers=owner_headers,
    )
    assert dev_del.status_code == 204

    # Revoking an employee's access
    # (DELETE /users/sources/{source_id}/accesses/{user_id})
    revoke: Response = await client.delete(
        f"/api/v1/users/sources/{source_id}/accesses/{staff_id}",
        headers=owner_headers,
    )
    assert revoke.status_code == 204
