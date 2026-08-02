import os
import tempfile
from datetime import date, datetime, timedelta

os.environ["DATABASE_URL"] = f"sqlite:///{tempfile.mkdtemp()}/test.db"
os.environ["SYNC_INTERVAL_MINUTES"] = "0"
os.environ["ADMIN_USER"] = "admin"
os.environ["ADMIN_PASSWORD"] = "test12345"

from fastapi.testclient import TestClient  # noqa: E402
import pytest  # noqa: E402

from backend.app import db  # noqa: E402
from backend.app.models import (  # noqa: E402
    Activity,
    GarminCred,
    HealthDay,
    PlanDay,
    User,
)
from backend.app.main import app  # noqa: E402

db.init_db()


@pytest.fixture(autouse=True)
def _clear_rate_limit():
    from backend.app import auth

    auth._rate.clear()
    yield


def seed_data(user_id: int = 1):
    with db.session() as s:
        base = datetime.now() - timedelta(days=2)
        s.add(
            Activity(
                user_id=user_id,
                garmin_id="test-run-1",
                name="Morgenlauf",
                sport="running",
                start_time=base,
                duration_seconds=2400,
                distance_km=8.0,
                avg_hr=148,
                max_hr=172,
                calories=500,
                avg_pace_min_km=5.0,
                hr_zones={"zone1": 120, "zone2": 300, "zone3": 600, "zone4": 300, "zone5": 60},
            )
        )
        s.add(
            Activity(
                user_id=user_id,
                garmin_id="test-cycle-1",
                name="Radtour",
                sport="cycling",
                start_time=base - timedelta(days=1),
                duration_seconds=3600,
                distance_km=30.0,
                avg_hr=130,
                calories=800,
            )
        )
        s.add(
            HealthDay(
                user_id=user_id,
                date=date.today(),
                sleep_seconds=7.5 * 3600,
                deep_sleep_seconds=1.5 * 3600,
                active_calories=400,
                resting_hr=52,
            )
        )
        s.add(
            PlanDay(
                user_id=user_id,
                week="2026-W31",
                day_offset=0,
                sport="running",
                focus="Lockerer Lauf",
                description="30 min Zone 2",
            )
        )
        s.commit()


def login(client: TestClient, username="admin", password="test12345"):
    return client.post(
        "/api/auth/login",
        json={"username": username, "password": password},
        headers={"Origin": "http://testserver"},
    )


def admin_id() -> int:
    with db.session() as s:
        return s.exec(
            __import__("sqlmodel").select(User).where(User.username == "admin")
        ).first().id


def test_health():
    with TestClient(app) as c:
        r = c.get("/api/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"


def test_admin_created_at_first_start():
    with db.session() as s:
        admin = s.exec(
            __import__("sqlmodel").select(User).where(User.username == "admin")
        ).first()
        assert admin is not None
        assert admin.is_admin is True


def test_login_success_and_logout():
    with TestClient(app) as c:
        r = login(c)
        assert r.status_code == 200
        assert r.json()["user"]["username"] == "admin"
        r = c.get("/api/auth/me")
        assert r.status_code == 200
        r = c.post("/api/auth/logout", headers={"Origin": "http://testserver"})
        assert r.status_code == 200
        r = c.get("/api/auth/me")
        assert r.status_code == 401


def test_login_wrong_password():
    with TestClient(app) as c:
        r = login(c, password="falsch123")
        assert r.status_code == 401


def test_csrf_required_on_post():
    with TestClient(app) as c:
        login(c)
        r = c.post("/api/auth/logout")  # ohne Origin-Header
        assert r.status_code == 403


def test_login_rate_limit():
    with TestClient(app) as c:
        for _ in range(5):
            c.post(
                "/api/auth/login",
                json={"username": "admin", "password": "falsch"},
                headers={"Origin": "http://testserver"},
            )
        r = c.post(
            "/api/auth/login",
            json={"username": "admin", "password": "falsch"},
            headers={"Origin": "http://testserver"},
        )
        assert r.status_code == 429


def test_activities_list_and_isolation():
    uid = admin_id()
    seed_data(uid)
    with TestClient(app) as c:
        login(c)
        r = c.get("/api/activities")
        data = r.json()
        assert data["total"] == 2
        assert data["items"][0]["sport"] in ("running", "cycling")

        # Zweiter User sieht keine Daten des ersten
        r = c.post(
            "/api/users",
            json={"username": "freundin", "password": "passwort123", "display_name": "Freundin"},
            headers={"Origin": "http://testserver"},
        )
        assert r.status_code == 200
        c.post("/api/auth/logout", headers={"Origin": "http://testserver"})
        login(c, "freundin", "passwort123")
        r = c.get("/api/activities")
        assert r.json()["total"] == 0


def test_activities_filter():
    with TestClient(app) as c:
        login(c)
        r = c.get("/api/activities?sport=running")
        data = r.json()
        assert data["total"] == 1
        assert data["items"][0]["sport"] == "running"


def test_stats_summary():
    with TestClient(app) as c:
        login(c)
        r = c.get("/api/stats/summary?days=7")
        data = r.json()
        assert data["totals"]["running_km"] == 8.0
        assert data["totals"]["cycling_km"] == 30.0
        assert data["totals"]["sessions"] == 2
        assert data["totals"]["avg_sleep_h"] == 7.5


def test_stats_zones():
    with TestClient(app) as c:
        login(c)
        r = c.get("/api/stats/zones?days=30")
        data = r.json()
        assert data["totals_seconds"]["zone3"] == 600
        assert data["total_minutes"] > 0


def test_stats_summary_includes_daily_activities():
    with TestClient(app) as c:
        login(c)
        r = c.get("/api/stats/summary?days=7")
        data = r.json()
        day = next((s for s in data["series"] if s["activities"]), None)
        assert day is not None
        act = day["activities"][0]
        assert act["sport"] in ("running", "cycling")
        assert "distance_km" in act
        assert act["duration_min"] > 0


def test_trend_endpoint_periods():
    with TestClient(app) as c:
        login(c)
        for period, expected in (("week", 7), ("month", 4), ("year", 12)):
            r = c.get(f"/api/stats/trend?period={period}")
            assert r.status_code == 200
            d = r.json()
            assert len(d["buckets"]) == expected
            assert d["totals"]["running_km"] == 8.0
        r = c.get("/api/stats/trend?period=x")
        assert r.status_code == 422


def test_stats_load():
    with TestClient(app) as c:
        login(c)
        r = c.get("/api/stats/load")
        d = r.json()
        assert "acute_7d" in d
        assert "ratio" in d
        assert "zone" in d


def test_stats_pbs():
    with db.session() as s:
        s.add(Activity(
            user_id=admin_id(), garmin_id="pb-run",
            name="PB-Lauf", sport="running",
            start_time=datetime.now() - timedelta(days=1),
            duration_seconds=1500, distance_km=5.0,
            avg_pace_min_km=5.0,
        ))
        s.commit()
    with TestClient(app) as c:
        login(c)
        r = c.get("/api/stats/pbs")
        items = r.json()["items"]
        five = next((p for p in items if p["label"] == "5k"), None)
        assert five is not None
        assert five["time_seconds"] == 1500  # 5.0 pace * 5 km


def test_goals_crud():
    with db.session() as s:
        s.add(Activity(
            user_id=admin_id(), garmin_id="goal-run", name="Ziel-Lauf", sport="running",
            start_time=datetime.now(), duration_seconds=1800, distance_km=5.0,
        ))
        s.commit()
    with TestClient(app) as c:
        login(c)
        r = c.put("/api/goals", json={"running_km": 50, "cycling_km": None}, headers={"Origin": "http://testserver"})
        assert r.status_code == 200
        r = c.get("/api/goals")
        d = r.json()
        assert d["running_goal"] == 50
        assert d["running_km"] >= 5.0
        assert d["running_progress"] == round(min(1.0, d["running_km"] / 50) * 100)


def test_race_plan_create_and_delete(monkeypatch):
    from backend.app import plan_service

    def fake_chat_json(system, user_prompt, db_user=None, **kwargs):
        return {
            "weeks": [
                {
                    "woche": 1,
                    "fokus": "Basiswoche",
                    "tage": [
                        {"tag": 0, "sport": "running", "fokus": "Locker", "beschreibung": "30 min", "steps": [{"typ": "recovery", "dauer_min": 30, "zone": 2}]},
                        {"tag": 2, "sport": "rest", "fokus": "Pause", "beschreibung": "", "steps": None},
                    ],
                }
            ]
        }

    monkeypatch.setattr("backend.app.plan_service.llm.chat_json", fake_chat_json)
    monkeypatch.setattr(
        "backend.app.routers.race.llm.status",
        lambda user=None: {"configured": True, "provider": "opencode", "model": "x"},
    )
    with TestClient(app) as c:
        login(c)
        r = c.post(
            "/api/race-plan",
            json={"name": "Testlauf", "target_date": "2026-10-01", "distance_km": 10},
            headers={"Origin": "http://testserver"},
        )
        assert r.status_code == 200
        assert r.json()["days"] == 2
        r = c.get("/api/race-plan")
        assert r.json()["ok"] is True
        assert r.json()["goal"]["name"] == "Testlauf"
        r = c.delete("/api/race-plan", headers={"Origin": "http://testserver"})
        assert r.status_code == 200
        r = c.get("/api/race-plan")
        assert r.json()["ok"] is False


def test_plan_toggle():
    with TestClient(app) as c:
        login(c)
        r = c.post("/api/plan/1/toggle", headers={"Origin": "http://testserver"})
        assert r.status_code == 200
        assert r.json()["done"] is True
        r = c.post("/api/plan/1/toggle", headers={"Origin": "http://testserver"})
        assert r.json()["done"] is False


def test_suggestion_empty():
    with TestClient(app) as c:
        login(c)
        r = c.get("/api/suggestion")
        data = r.json()
        assert data["ok"] is False


def test_settings():
    with TestClient(app) as c:
        login(c)
        r = c.get("/api/settings")
        data = r.json()
        assert "llm" in data
        assert data["garmin_configured"] is False
        assert data["user"]["username"] == "admin"


def test_upload_rejects_invalid():
    with TestClient(app) as c:
        login(c)
        r = c.post(
            "/api/upload/fit",
            files={"files": ("x.fit", b"not a fit file", "application/octet-stream")},
            headers={"Origin": "http://testserver"},
        )
        assert r.status_code == 200
        result = r.json()["results"][0]
        assert result["imported"] is False
        assert result["error"]


def test_admin_users_crud():
    with TestClient(app) as c:
        login(c)
        r = c.post(
            "/api/users",
            json={"username": "testuser", "password": "passwort123", "display_name": "T"},
            headers={"Origin": "http://testserver"},
        )
        assert r.status_code == 200
        uid = r.json()["user"]["id"]
        r = c.get("/api/users")
        names = [u["username"] for u in r.json()["items"]]
        assert "testuser" in names
        r = c.post(
            f"/api/users/{uid}/password",
            json={"password": "neuespasswort"},
            headers={"Origin": "http://testserver"},
        )
        assert r.status_code == 200
        r = c.delete(f"/api/users/{uid}", headers={"Origin": "http://testserver"})
        assert r.status_code == 200
        r = c.get("/api/users")
        names = [u["username"] for u in r.json()["items"]]
        assert "testuser" not in names


def test_non_admin_cannot_list_users():
    with TestClient(app) as c:
        login(c)
        c.post(
            "/api/users",
            json={"username": "freundin2", "password": "passwort123"},
            headers={"Origin": "http://testserver"},
        )
        c.post("/api/auth/logout", headers={"Origin": "http://testserver"})
        login(c, "freundin2", "passwort123")
        r = c.get("/api/users")
        assert r.status_code == 403


def test_llm_override_per_user():
    with TestClient(app) as c:
        login(c)
        r = c.put(
            "/api/users/me/llm",
            json={"provider": "ollama", "base_url": "http://localhost:11434/v1", "model": "llama3.2", "api_key": ""},
            headers={"Origin": "http://testserver"},
        )
        assert r.status_code == 200
        r = c.get("/api/settings")
        assert r.json()["llm"]["provider"] == "ollama"


STEPS = [
    {"typ": "warmup", "dauer_min": 10, "zone": 2},
    {"typ": "interval", "dauer_min": 5, "zone": 4},
    {"typ": "recovery", "dauer_min": 2, "zone": None},
    {"typ": "cooldown", "dauer_min": 10, "zone": 2},
]


def test_build_workout_running():
    from backend.app.garmin_workouts import build_workout

    w = build_workout("Tempolauf", "running", STEPS)
    assert w["workoutName"] == "Tempolauf"
    assert w["sportType"]["sportTypeKey"] == "running"
    assert w["estimatedDurationInSecs"] == 27 * 60
    steps = w["workoutSegments"][0]["workoutSteps"]
    assert len(steps) == 4
    assert steps[0]["stepType"]["stepTypeKey"] == "warmup"
    assert steps[1]["targetType"]["zoneNumber"] == 4
    assert steps[1]["endConditionValue"] == 300


def test_build_workout_unsupported():
    from backend.app.garmin_workouts import UnsupportedSportError, build_workout

    try:
        build_workout("Schwimmen", "swimming", STEPS)
        assert False
    except UnsupportedSportError:
        pass


def test_build_workout_strength():
    from backend.app.garmin_workouts import build_workout

    w = build_workout(
        "Ganzkörper",
        "strength",
        None,
        kraft_steps=[
            {"uebung": "Kniebeuge", "saetze": 3, "wiederholungen": 10, "gewicht_kg": 60},
            {"uebung": "Bankdrücken", "saetze": 3, "wiederholungen": 8, "gewicht_kg": 50},
        ],
    )
    assert w["sportType"]["sportTypeKey"] == "strength_training"
    steps = w["workoutSegments"][0]["workoutSteps"]
    assert len(steps) == 2
    assert steps[0]["type"] == "RepeatGroupDTO"
    assert steps[0]["numberOfIterations"] == 3


def test_build_workout_strength_no_exercises():
    from backend.app.garmin_workouts import NoStepsError, build_workout

    try:
        build_workout("Kraft", "strength", None, kraft_steps=[])
        assert False
    except NoStepsError:
        pass


def test_build_workout_no_steps():
    from backend.app.garmin_workouts import NoStepsError, build_workout

    try:
        build_workout("Lauf", "running", None)
        assert False
    except NoStepsError:
        pass


def test_workout_endpoints_require_credentials():
    with TestClient(app) as c:
        login(c)
        r = c.post("/api/garmin/workout/suggestion/1", headers={"Origin": "http://testserver"})
        assert r.status_code == 400
        r = c.post("/api/garmin/workout/plan/1", headers={"Origin": "http://testserver"})
        assert r.status_code == 400
        r = c.post(
            "/api/garmin/workout/plan-all?week=2026-W31",
            headers={"Origin": "http://testserver"},
        )
        assert r.status_code == 400


def test_push_workout_to_devices(monkeypatch):
    class FakeApi:
        def __init__(self):
            self.pushed = []

        def get_devices(self):
            return [
                {"appSupport": True, "deviceId": 1, "applicationKey": "forerunner"},
                {"appSupport": False, "deviceId": 2, "applicationKey": "hrm"},
            ]

        def push_workout_to_device(self, workout_id, device_id):
            self.pushed.append((workout_id, device_id))

    fake = FakeApi()
    monkeypatch.setattr(
        "backend.app.garmin_workouts._connected_api", lambda user_id: fake
    )
    from backend.app.garmin_workouts import push_workout_to_devices

    results = push_workout_to_devices(1, "123")
    assert results[0]["ok"] is True
    assert fake.pushed == [("123", 1)]


def test_sport_id_mapping():
    from backend.app.sports import map_sport_id

    assert map_sport_id(1) == "running"
    assert map_sport_id(2) == "cycling"
    assert map_sport_id(44) == "strength"
    assert map_sport_id(21) == "cycling"
    assert map_sport_id(999) == "other"


def test_zones_garmin_list_format():
    from backend.app.sports import normalize_zones

    zones = normalize_zones(
        [
            {"zoneNumber": 1, "secsInZone": 119.995},
            {"zoneNumber": 2, "secsInZone": 356.996},
            {"zoneNumber": 3, "secsInZone": 2865.957},
            {"zoneNumber": 4, "secsInZone": 2720.977},
            {"zoneNumber": 5, "secsInZone": 0.0},
        ]
    )
    assert zones["zone1"] == 119
    assert zones["zone3"] == 2865
    assert zones["zone5"] == 0


def test_normalize_activity_from_list_api():
    from backend.app.garmin_sync import _normalize_activity

    row = _normalize_activity(
        {
            "activityId": "42",
            "activityName": "Wolfenbüttel Rennradfahren",
            "sportType": None,
            "sportTypeId": 2,
            "startTimeLocal": "2026-08-01 13:29:36",
            "distance": 40714.69,
            "duration": 6071.79,
            "averageHR": 158.0,
            "calories": 981.0,
            "hrTimeInZone_1": 120,
            "hrTimeInZone_2": 357,
            "hrTimeInZone_3": 2866,
            "hrTimeInZone_4": 2721,
            "hrTimeInZone_5": 0,
        }
    )
    assert row["sport"] == "cycling"
    assert row["distance_km"] == 40.715
    assert row["avg_hr"] == 158.0
    assert row["hr_zones"]["zone3"] == 2866


def test_device_classification():
    from backend.app.garmin_workouts import classify_device

    assert classify_device("forerunner57047MM") == "watch"
    assert classify_device("fenix7") == "watch"
    assert classify_device("edge540") == "bike_computer"
    assert classify_device("HRM200") == "hrm"
    assert classify_device("") == "other"


def test_default_device_ids():
    from backend.app.garmin_workouts import default_device_ids

    devices = [
        {"device_id": "1", "kind": "watch", "name": "Forerunner"},
        {"device_id": "2", "kind": "bike_computer", "name": "Edge"},
        {"device_id": "3", "kind": "hrm", "name": "HRM"},
    ]
    assert default_device_ids(devices, "running") == ["1"]
    assert default_device_ids(devices, "cycling") == ["2"]
    # Fallback: nur "other" vorhanden
    assert default_device_ids([{"device_id": "9", "kind": "other", "name": "X"}], "running") == ["9"]


def test_push_to_selected_devices(monkeypatch):
    from backend.app import garmin_workouts as gw

    class FakeApi:
        def __init__(self):
            self.pushed = []

        def get_devices(self):
            return [
                {"appSupport": True, "deviceId": 1, "applicationKey": "forerunner570"},
                {"appSupport": True, "deviceId": 2, "applicationKey": "edge540"},
                {"appSupport": False, "deviceId": 3, "applicationKey": "hrm200"},
            ]

        def push_workout_to_device(self, workout_id, device_id):
            self.pushed.append((workout_id, device_id))

    fake = FakeApi()
    monkeypatch.setattr("backend.app.garmin_workouts._connected_api", lambda user_id: fake)

    results = gw.push_workout_to_devices(1, "123", device_ids=["2"])
    assert results[0]["ok"] is True
    assert fake.pushed == [("123", 2)]
    assert len(results) == 1


def test_sleep_parsing_with_list_sleeplevels(monkeypatch):
    """Alte Garmin-Tage liefern sleepLevels als Liste statt Dict – darf nicht crashen."""
    from datetime import date

    from backend.app import garmin_sync as g
    from backend.app.models import HealthDay

    class FakeApi:
        def get_stats(self, d):
            return {"restingHeartRate": 46, "totalSteps": 500, "totalKilocalories": 2500}

        def get_sleep_data(self, d):
            return {
                "dailySleepDTO": {"sleepTimeSeconds": 33460},
                "sleepLevels": [{"name": "deep", "seconds": 5400}],  # LISTE!
                "avgOvernightHrv": 80.0,
                "hrvStatus": "BALANCED",
            }

    monkeypatch.setattr("backend.app.garmin_sync._api_get", lambda api, call, retries=3: call())
    monkeypatch.setattr("backend.app.garmin_sync._user_timezone", lambda api: "UTC")
    target_date = date.today() - timedelta(days=2)
    with db.session() as s:
        existing = s.get(HealthDay, (1, target_date))
        if existing:
            s.delete(existing)
            s.commit()
        g._sync_health(s, 1, FakeApi(), days=5)
        s.commit()
    with db.session() as s:
        h = s.get(HealthDay, (1, target_date))
        assert h is not None
        assert h.sleep_seconds == 33460
        assert h.hrv_avg == 80.0


def test_generate_suggestion_sets_user_id(monkeypatch):
    from backend.app import plan_service

    def fake_chat_json(system, user_prompt, db_user=None):
        assert db_user is not None
        return {
            "titel": "Testlauf",
            "sport": "running",
            "begruendung": "weil",
            "training": "30 min locker",
            "steps": [{"typ": "warmup", "dauer_min": 5, "zone": 1}],
        }

    monkeypatch.setattr("backend.app.plan_service.llm.chat_json", fake_chat_json)
    sug = plan_service.generate_suggestion(admin_id())
    assert sug.user_id == admin_id()
    assert sug.title == "Testlauf"


def test_credentials_encrypted_at_rest():
    uid = admin_id()
    with TestClient(app) as c:
        login(c)
        r = c.post(
            "/api/garmin/credentials",
            json={"email": "test@example.com", "password": "geheim"},
            headers={"Origin": "http://testserver"},
        )
        assert r.status_code == 200
    with db.session() as s:
        row = s.get(GarminCred, uid)
        assert row is not None
        assert "geheim" not in row.password
        assert row.password != "geheim"
