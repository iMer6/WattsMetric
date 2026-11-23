from datetime import datetime, timedelta, timezone
from uuid import uuid4
from httpx import AsyncClient, Response
from app.core.enums import RoleName


async def test_telemetry_idempotent_batch_and_time_guards(
    client: AsyncClient,
    make_user_headers,
):
    """
    Verification of packet reception idempotency from the ESP32
    and the 31-day window limit.
    """
    owner_headers: dict[str, str] = await make_user_headers(
        "telem_owner@wattsmetric.ua", RoleName.OWNER
    )

    source_id = (
        await client.post(
            "/api/v1/sources",
            headers=owner_headers,
            json={
                "source_name": "SPP Telemetry",
                "qr_code": "QR-TEL-01",
                "source_status": "active"
            },
        )
    ).json()["source_id"]

    iot_id = (
        await client.post(
            f"/api/v1/iot/{source_id}/devices",
            headers=owner_headers,
            json={
                "esp32_mac": "AA:11:22:33:44:55",
                "firmware_version": "1.0.0",
                "sensor_config": {},
                "is_active": True,
            },
        )
    ).json()["iot_id"]

    t0: datetime = datetime(
        2026, 11, 23, 12, 0, 0,
        tzinfo=timezone.utc
    )
    batch_url: str = (
        f"/api/v1/telemetry/{source_id}/devices/{iot_id}/measurements/batch"
    )
    batch_data: dict[str, list[dict[str, str | float]]] = {
        "measurements": [
            {
                "measured_at": (t0 + timedelta(seconds=i * 30)).isoformat(),
                "voltage": 228.5,
                "current": 8.4,
                "temperature": 22.0,
                "illumination": 780.0,
            }
            for i in range(4)
        ]
    }

    # Initial transmission and retransmission (simulating ACK loss on ESP32)
    r1: Response = await client.post(
        batch_url, headers=owner_headers, json=batch_data
    )
    r2: Response = await client.post(
        batch_url, headers=owner_headers, json=batch_data
    )
    assert r1.status_code == 201
    assert r2.status_code == 201

    # Verify that there are exactly 4 records in the database
    # (ON CONFLICT worked, resulting in no duplicates).
    list_resp: Response = await client.get(
        f"/api/v1/telemetry/{source_id}/devices/{iot_id}/measurements",
        headers=owner_headers,
        params={
            "date_from": (t0 - timedelta(minutes=1)).isoformat(),
            "date_to": (t0 + timedelta(minutes=5)).isoformat(),
        },
    )
    assert list_resp.status_code == 200
    assert list_resp.json()["total"] == 4
    assert list_resp.headers["X-Total-Count"] == "4"

    # A request for a period > 31 days must be rejected (422)
    too_wide_resp: Response = await client.get(
        f"/api/v1/telemetry/{source_id}/devices/{iot_id}/measurements",
        headers=owner_headers,
        params={
            "date_from": (t0 - timedelta(days=40)).isoformat(),
            "date_to": t0.isoformat(),
        },
    )
    assert too_wide_resp.status_code == 422


async def test_maintenance_offline_sync_and_photo_upload(
    client: AsyncClient,
    make_user_headers,
):
    """
    Synchronization of the offline session by the engineer
    and validation of the uploaded photos.
    """
    owner_headers: dict[str, str] = await make_user_headers(
        "maint_owner@wattsmetric.ua", RoleName.OWNER
    )
    eng_headers: dict[str, str] = await make_user_headers(
        "maint_eng@wattsmetric.ua", RoleName.ENGINEER
    )

    eng_id = (
        await client.get(
            "/api/v1/users/me",
            headers=eng_headers
        )
    ).json()["user_id"]
    roles = (
        await client.get(
            "/api/v1/users/roles",
            headers=owner_headers
        )
    ).json()
    eng_role_id = next(
        role["role_id"]
        for role in roles
        if role["role_name"] == RoleName.ENGINEER.value
    )

    source_id = (
        await client.post(
            "/api/v1/sources",
            headers=owner_headers,
            json={
                "source_name": "SPP Service",
                "qr_code": "QR-MNT-01",
                "source_status": "active"
            },
        )
    ).json()["source_id"]

    # The owner appoints an engineer to the site.
    grant: Response = await client.post(
        f"/api/v1/users/sources/{source_id}/accesses",
        headers=owner_headers,
        json={
            "user_id": eng_id,
            "role_id": eng_role_id
        },
    )
    assert grant.status_code == 201

    # Offline service session synchronization
    session_id: str = str(uuid4())
    offline_time: str = datetime(
        2026, 11, 23, 9, 30,
        tzinfo=timezone.utc
    ).isoformat()
    
    sess_resp: Response = await client.post(
        f"/api/v1/maintenance/{source_id}/maintenance-sessions",
        headers=eng_headers,
        json={
            "session_id": session_id,
            "inspector_notes": "Cleaning dust from photovoltaic modules.",
            "created_at": offline_time,
            "inspector_ids": [eng_id],
        },
    )
    assert sess_resp.status_code == 201
    assert sess_resp.json()["is_synced"] is True

    # Uploading a forged file (invalid magic bytes)
    bad_photo: Response = await client.post(
        f"/api/v1/maintenance/{source_id}/maintenance-sessions/{session_id}/photos",
        headers=eng_headers,
        files={
            "file": (
                "fake.jpg",
                b"not_a_real_jpeg_content",
                "image/jpeg"
            )
        },
    )
    assert bad_photo.status_code in (400, 415, 422)

    # Uploading a valid minimal JPEG (magic bytes FF D8 FF E0)
    minimal_jpeg: bytes = (
        b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
        b"\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t\x08\n\x0c"
        b"\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a\x1f\x1e\x1d\x1a\x1c"
        b"\x1c $.' \",#\x1c\x1c(7),01444\x1f'9=82<.342\xff\xc0\x00\x0b\x08\x00"
        b"\x01\x00\x01\x01\x01\x11\x00\xff\xda\x00\x08\x01\x01\x00\x00?\x00\xbf\x00\xff\xd9"
    )
    good_photo: Response = await client.post(
        f"/api/v1/maintenance/{source_id}/maintenance-sessions/{session_id}/photos",
        headers=eng_headers,
        files={
            "file": (
                "panel_check.jpg",
                minimal_jpeg,
                "image/jpeg"
            )
        },
        data={
            "created_at": offline_time
        },
    )
    assert good_photo.status_code == 201


async def test_profiles_aggregates_and_maintenance_inspectors(
    client: AsyncClient,
    make_user_headers,
):
    """
    Test profiles, time-based aggregates, list of technical inspection sessions,
    and inspector assignments.
    """
    owner_headers: dict[str, str] = await make_user_headers(
        "analyt_owner@wattsmetric.ua", RoleName.OWNER
    )
    eng1_headers: dict[str, str] = await make_user_headers(
        "eng1@wattsmetric.ua", RoleName.ENGINEER
    )
    eng2_headers: dict[str, str] = await make_user_headers(
        "eng2@wattsmetric.ua", RoleName.ENGINEER
    )

    eng1_id = (
        await client.get(
            "/api/v1/users/me",
            headers=eng1_headers
        )
    ).json()["user_id"]
    eng2_id = (
        await client.get(
            "/api/v1/users/me",
            headers=eng2_headers
        )
    ).json()["user_id"]
    roles = (
        await client.get(
            "/api/v1/users/roles",
            headers=owner_headers
        )
    ).json()
    eng_role_id = next(
        role["role_id"]
        for role in roles
        if role["role_name"] == RoleName.ENGINEER.value
    )

    # Test profiles: POST ==> GET list ==> GET detail
    prof_resp: Response = await client.post(
        "/api/v1/telemetry/test-profiles",
        headers=owner_headers,
        json={
            "test_profile_name": "Summer peak for SPP 2026",
            "profile_payload": {
                "points": [{"t": 0, "v": 230.0, "i": 12.0}]
            },
        },
    )
    assert prof_resp.status_code == 201
    profile_id = prof_resp.json()["test_profile_id"]

    prof_list: Response = await client.get(
        "/api/v1/telemetry/test-profiles",
        headers=owner_headers,
        params={"search": "Summer"},
    )
    assert prof_list.status_code == 200
    assert prof_list.json()["total"] >= 1

    prof_detail = await client.get(
        f"/api/v1/telemetry/test-profiles/{profile_id}",
        headers=owner_headers,
    )
    assert prof_detail.status_code == 200
    assert "points" in prof_detail.json()["profile_payload"]

    # Creating a source and checking GET /aggregates
    source_id = (
        await client.post(
            "/api/v1/sources",
            headers=owner_headers,
            json={
                "source_name": "SPP Analytics",
                "qr_code": "QR-ANL-01",
                "source_status": "active"
            },
        )
    ).json()["source_id"]

    t0: datetime = datetime(
        2026, 11, 23, 0, 0,
        tzinfo=timezone.utc
    )
    agg_resp: Response = await client.get(
        f"/api/v1/telemetry/{source_id}/aggregates",
        headers=owner_headers,
        params={
            "date_from": t0.isoformat(),
            "date_to": (t0 + timedelta(days=7)).isoformat(),
        },
    )
    assert agg_resp.status_code == 200
    assert "items" in agg_resp.json()

    # Assign two engineers to the station,
    # create a maintenance session,
    # assign a second inspector, and read the list.
    for uid in (eng1_id, eng2_id):
        await client.post(
            f"/api/v1/users/sources/{source_id}/accesses",
            headers=owner_headers,
            json={
                "user_id": uid,
                "role_id": eng_role_id
            },
        )

    session_id: str = str(uuid4())
    await client.post(
        f"/api/v1/maintenance/{source_id}/maintenance-sessions",
        headers=eng1_headers,
        json={
            "session_id": session_id,
            "inspector_notes": "Scheduled maintenance of the inverter group.",
            "created_at": t0.isoformat(),
            "inspector_ids": [eng1_id],
        },
    )

    # POST /users/maintenance-sessions/{session_id}/inspectors
    assign_resp: Response = await client.post(
        f"/api/v1/users/maintenance-sessions/{session_id}/inspectors",
        headers=owner_headers,
        json={"inspector_id": eng2_id},
    )
    assert assign_resp.status_code == 204

    # GET /maintenance/{source_id}/maintenance-sessions
    sessions_list: Response = await client.get(
        f"/api/v1/maintenance/{source_id}/maintenance-sessions",
        headers=owner_headers,
    )
    assert sessions_list.status_code == 200
    assert sessions_list.json()["total"] == 1
    assert len(sessions_list.json()["items"][0]["inspectors"]) == 2
