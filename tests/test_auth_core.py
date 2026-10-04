import threading
from datetime import datetime, timedelta, timezone

import pytest

from backend.auth import codes, quota, requests as reqs, sessions
from backend.auth.codes import RedeemResult
from backend.auth.ratelimit import RateLimiter
from backend.config import Settings
from backend.db import connect_app

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
EMAIL = "tech@example.com"


@pytest.fixture
def db(tmp_path):
    return tmp_path / "app.db"


@pytest.fixture
def conn(db):
    return connect_app(db)


@pytest.fixture
def settings():
    return Settings(_env_file=None, daily_question_limit=3, global_daily_limit=5)


def approve(conn, email=EMAIL):
    reqs.create_request(conn, email, "Tess", "Acme", "", NOW)
    reqs.set_status(conn, email, "approved", NOW)


def test_migrations_idempotent(db):
    connect_app(db).close()
    c = connect_app(db)
    assert c.execute("PRAGMA user_version").fetchone()[0] == 1


def test_requests_normalise_and_dedupe(conn):
    assert reqs.normalize_email("  Tech@Example.COM ") == EMAIL
    assert reqs.valid_email(EMAIL) and not reqs.valid_email("nope@") and not reqs.valid_email("a b@c.d")
    assert reqs.create_request(conn, EMAIL, "Tess", "Acme", "pumps", NOW) is True
    assert reqs.create_request(conn, EMAIL, "Tess", "Acme", "again", NOW) is False
    assert reqs.get_request(conn, EMAIL)["status"] == "pending"
    rows = reqs.list_requests(conn, ("pending",), NOW)
    assert rows[0]["email"] == EMAIL and rows[0]["questions_today"] == 0


def test_code_format_and_normalisation():
    code = codes.generate_code()
    assert len(code) == 9 and code[4] == "-"
    assert set(code.replace("-", "")) <= set(codes.ALPHABET)
    assert codes.normalize_code(" k7qm 4xpd ") == "K7QM-4XPD"
    assert codes.normalize_code("K7QM4XPD") == "K7QM-4XPD"


def test_redeem_accepts_loose_code_format(conn):
    approve(conn)
    code = codes.issue_code(conn, EMAIL, NOW)
    loose = code.replace("-", " ").lower()
    assert codes.redeem_code(conn, " TECH@example.com", loose, "1.1.1.1", NOW) is RedeemResult.OK


def test_redeem_rules(conn):
    approve(conn)
    code = codes.issue_code(conn, EMAIL, NOW)
    assert codes.redeem_code(conn, EMAIL, "AAAA-AAAA", "ip", NOW) is RedeemResult.INVALID
    assert codes.redeem_code(conn, EMAIL, code, "ip", NOW + timedelta(days=8)) is RedeemResult.INVALID  # expired
    assert codes.redeem_code(conn, EMAIL, code, "ip", NOW) is RedeemResult.OK
    assert codes.redeem_code(conn, EMAIL, code, "ip", NOW) is RedeemResult.INVALID  # single use


def test_redeem_requires_approval(conn):
    reqs.create_request(conn, EMAIL, "Tess", "Acme", "", NOW)
    code = codes.issue_code(conn, EMAIL, NOW)
    assert codes.redeem_code(conn, EMAIL, code, "ip", NOW) is RedeemResult.INVALID


def test_lockout_after_five_failures(conn):
    approve(conn)
    code = codes.issue_code(conn, EMAIL, NOW)
    for i in range(5):
        assert codes.redeem_code(conn, EMAIL, "BBBB-BBBB", "ip", NOW + timedelta(minutes=i)) is RedeemResult.INVALID
    assert codes.redeem_code(conn, EMAIL, code, "ip", NOW + timedelta(minutes=6)) is RedeemResult.LOCKED
    assert codes.redeem_code(conn, EMAIL, code, "ip", NOW + timedelta(minutes=70)) is RedeemResult.OK


def test_sessions(conn):
    approve(conn)
    token = sessions.create_session(conn, EMAIL, NOW)
    assert sessions.session_email(conn, token, NOW) == EMAIL
    assert sessions.session_email(conn, token, NOW + timedelta(days=31)) is None
    assert sessions.session_email(conn, "forged", NOW) is None
    reqs.set_status(conn, EMAIL, "revoked", NOW)
    assert sessions.session_email(conn, token, NOW) is None
    reqs.set_status(conn, EMAIL, "approved", NOW)
    sessions.delete_sessions(conn, EMAIL)
    assert sessions.session_email(conn, token, NOW) is None


def test_quota_user_limit_override_release_and_admin(conn, settings):
    approve(conn)
    for left in (2, 1, 0):
        st = quota.try_reserve(conn, EMAIL, settings, NOW)
        assert st.allowed and st.remaining == left and st.limit == 3
    st = quota.try_reserve(conn, EMAIL, settings, NOW)
    assert not st.allowed and st.reason == "user" and st.resets_at == "2026-10-02T00:00:00+00:00"
    quota.release(conn, EMAIL, NOW)
    assert quota.status(conn, EMAIL, settings, NOW).remaining == 1
    reqs.set_daily_limit(conn, EMAIL, 10)
    assert quota.status(conn, EMAIL, settings, NOW).remaining == 8
    quota.reset_today(conn, EMAIL, NOW)
    assert quota.status(conn, EMAIL, settings, NOW).remaining == 10
    assert quota.try_reserve(conn, "admin", settings, NOW, is_admin=True).allowed
    assert quota.try_reserve(conn, EMAIL, settings, NOW + timedelta(days=1)).remaining == 9  # new UTC day


def test_global_limit(conn, settings):
    for i in range(3):
        approve(conn, f"u{i}@x.com")
    used = 0
    for i in range(3):
        for _ in range(2):
            if quota.try_reserve(conn, f"u{i}@x.com", settings, NOW).allowed:
                used += 1
    assert used == 5
    st = quota.try_reserve(conn, "u2@x.com", settings, NOW)
    assert not st.allowed and st.reason == "global"


def test_reserve_is_atomic_at_limit(db, settings):
    c = connect_app(db)
    approve(c)
    quota.try_reserve(c, EMAIL, settings, NOW)
    quota.try_reserve(c, EMAIL, settings, NOW)  # one question left
    barrier = threading.Barrier(2)
    results = []
    exceptions = []

    def worker():
        try:
            conn = connect_app(db)
            barrier.wait()
            results.append(quota.try_reserve(conn, EMAIL, settings, NOW).allowed)
        except Exception as e:
            exceptions.append(e)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not exceptions, f"Exceptions in workers: {exceptions}"
    assert sorted(results) == [False, True]


def test_rate_limiter_window():
    t = [0.0]
    rl = RateLimiter(2, 60, clock=lambda: t[0])
    assert rl.hit("k") and rl.hit("k") and not rl.hit("k")
    assert rl.hit("other")
    t[0] = 61
    assert rl.hit("k")


def test_rate_limiter_blocked_does_not_record():
    t = [0.0]
    rl = RateLimiter(2, 60, clock=lambda: t[0])
    assert not rl.blocked("k") and not rl.blocked("k") and not rl.blocked("k")
    rl.hit("k"); rl.hit("k")
    assert rl.blocked("k")
    t[0] = 61
    assert not rl.blocked("k")


def test_concurrent_redeem_is_single_use(db, settings):
    c = connect_app(db)
    approve(c)
    code = codes.issue_code(c, EMAIL, NOW)
    barrier = threading.Barrier(2)
    results = []
    exceptions = []

    def worker():
        try:
            conn = connect_app(db)
            barrier.wait()
            results.append(codes.redeem_code(conn, EMAIL, code, "1.1.1.1", NOW))
        except Exception as e:
            exceptions.append(e)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not exceptions, f"Exceptions in workers: {exceptions}"
    assert sorted([r.value for r in results]) == ["invalid", "ok"]


def test_lockout_ignores_email_case(conn):
    approve(conn)
    code = codes.issue_code(conn, EMAIL, NOW)
    for i in range(5):
        assert codes.redeem_code(conn, " TECH@Example.COM ", "BBBB-BBBB", "ip", NOW + timedelta(minutes=i)) is RedeemResult.INVALID
    assert codes.redeem_code(conn, EMAIL, code, "ip", NOW + timedelta(minutes=6)) is RedeemResult.LOCKED


def test_secrets_stored_only_as_hashes(conn):
    approve(conn)
    code = codes.issue_code(conn, EMAIL, NOW)
    token = sessions.create_session(conn, EMAIL, NOW)

    # Verify code is stored only as hash
    code_row = conn.execute("SELECT code_hash FROM access_codes WHERE email = ?", (EMAIL,)).fetchone()
    assert code_row["code_hash"] == codes.hash_secret(code)
    assert code not in code_row["code_hash"]

    # Verify no plaintext code appears in any access_codes column
    all_code_columns = conn.execute(f"PRAGMA table_info(access_codes)").fetchall()
    for col_row in all_code_columns:
        col_name = col_row["name"]
        col_data = conn.execute(f"SELECT {col_name} FROM access_codes WHERE email = ?", (EMAIL,)).fetchone()
        if col_data[col_name] is not None:
            assert code not in str(col_data[col_name])

    # Verify token is stored only as hash
    token_row = conn.execute("SELECT token_hash FROM sessions WHERE email = ?", (EMAIL,)).fetchone()
    assert token_row["token_hash"] == codes.hash_secret(token)
    assert token not in token_row["token_hash"]

    # Verify no plaintext token appears in any sessions column
    all_token_columns = conn.execute(f"PRAGMA table_info(sessions)").fetchall()
    for col_row in all_token_columns:
        col_name = col_row["name"]
        col_data = conn.execute(f"SELECT {col_name} FROM sessions WHERE email = ?", (EMAIL,)).fetchone()
        if col_data[col_name] is not None:
            assert token not in str(col_data[col_name])


def test_release_never_negative(conn, settings):
    approve(conn)
    # Release without prior reserve
    quota.release(conn, EMAIL, NOW)
    st = quota.status(conn, EMAIL, settings, NOW)
    assert st.remaining == 3

    # Release again
    quota.release(conn, EMAIL, NOW)
    st = quota.status(conn, EMAIL, settings, NOW)
    assert st.remaining == 3
