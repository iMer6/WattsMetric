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
