import hashlib, random, time, logging, json
from datetime import datetime, timedelta
from contextlib import contextmanager

import psycopg2
import psycopg2.pool
import psycopg2.extras
import redis

from config import (
    PG_HOST, PG_PORT, PG_DB, PG_USER, PG_PASS,
    REDIS_HOST, REDIS_PORT, REDIS_DB,
    SECRET_SALT, HISTORY_LIMIT,
    BONUS_BIENVENUE, BONUS_PARRAIN, BONUS_FILLEUL,
    ABSENCE_BONUS_H, ABSENCE_BONUS_XAF
)

log = logging.getLogger(__name__)

pg_pool = psycopg2.pool.ThreadedConnectionPool(
    minconn=2, maxconn=20,
    host=PG_HOST, port=PG_PORT,
    dbname=PG_DB, user=PG_USER, password=PG_PASS
)

redis_client = redis.Redis(
    host=REDIS_HOST, port=REDIS_PORT, db=REDIS_DB,
    decode_responses=True
)

@contextmanager
def pg():
    conn = pg_pool.getconn()
    try:
        conn.autocommit = False
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        pg_pool.putconn(conn)

def r():
    return redis_client

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id      BIGINT PRIMARY KEY,
    username     TEXT DEFAULT '',
    tg_name      TEXT DEFAULT '',
    custom_name  TEXT DEFAULT '',
    solde        NUMERIC(12,2) DEFAULT 0,
    pin_hash     TEXT NOT NULL,
    parrain_id   BIGINT DEFAULT NULL REFERENCES users(user_id),
    filleuls_cnt INTEGER DEFAULT 0,
    total_mises  INTEGER DEFAULT 0,
    total_gains  NUMERIC(12,2) DEFAULT 0,
    meilleur_gain NUMERIC(12,2) DEFAULT 0,
    is_banned    BOOLEAN DEFAULT FALSE,
    created_at   TIMESTAMPTZ DEFAULT NOW(),
    last_seen    TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS history (
    id            SERIAL PRIMARY KEY,
    numero        SMALLINT NOT NULL CHECK(numero BETWEEN 0 AND 5),
    tour_id       TEXT NOT NULL,
    winner_uid    BIGINT DEFAULT NULL,
    winner_name   TEXT DEFAULT NULL,
    winner_solde  NUMERIC(12,2) DEFAULT NULL,
    total_players INTEGER DEFAULT 0,
    total_mise    NUMERIC(12,2) DEFAULT 0,
    drawn_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS bets (
    id          SERIAL PRIMARY KEY,
    user_id     BIGINT NOT NULL REFERENCES users(user_id),
    tour_id     TEXT NOT NULL,
    numero      SMALLINT NOT NULL CHECK(numero BETWEEN 0 AND 5),
    mise        NUMERIC(12,2) NOT NULL,
    statut      TEXT DEFAULT 'EN_ATTENTE',
    gain        NUMERIC(12,2) DEFAULT 0,
    placed_at   TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS depot_demandes (
    id         SERIAL PRIMARY KEY,
    user_id    BIGINT NOT NULL REFERENCES users(user_id),
    montant    NUMERIC(12,2) NOT NULL,
    telephone  TEXT DEFAULT '',
    statut     TEXT DEFAULT 'EN_ATTENTE',
    note       TEXT DEFAULT '',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    validated_at TIMESTAMPTZ DEFAULT NULL
);

CREATE TABLE IF NOT EXISTS retrait_demandes (
    id         SERIAL PRIMARY KEY,
    user_id    BIGINT NOT NULL REFERENCES users(user_id),
    montant    NUMERIC(12,2) NOT NULL,
    telephone  TEXT NOT NULL,
    statut     TEXT DEFAULT 'EN_ATTENTE',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    validated_at TIMESTAMPTZ DEFAULT NULL
);

CREATE TABLE IF NOT EXISTS parrainage (
    id          SERIAL PRIMARY KEY,
    parrain_id  BIGINT NOT NULL REFERENCES users(user_id),
    filleul_id  BIGINT NOT NULL UNIQUE REFERENCES users(user_id),
    bonus_verse BOOLEAN DEFAULT TRUE,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS server_state (
    id         INTEGER PRIMARY KEY DEFAULT 1,
    is_open    BOOLEAN DEFAULT FALSE,
    open_at    TIMESTAMPTZ DEFAULT NULL,
    open_key   TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS leaderboard_daily (
    id         SERIAL PRIMARY KEY,
    user_id    BIGINT NOT NULL REFERENCES users(user_id),
    gains_jour NUMERIC(12,2) DEFAULT 0,
    date_jour  DATE DEFAULT CURRENT_DATE,
    UNIQUE(user_id, date_jour)
);

CREATE INDEX IF NOT EXISTS idx_bets_user    ON bets(user_id);
CREATE INDEX IF NOT EXISTS idx_bets_tour    ON bets(tour_id);
CREATE INDEX IF NOT EXISTS idx_history_date ON history(drawn_at DESC);
CREATE INDEX IF NOT EXISTS idx_users_parrain ON users(parrain_id);

INSERT INTO server_state(id, is_open, open_key)
VALUES(1, FALSE, 'NKAP_CLOSED')
ON CONFLICT(id) DO NOTHING;
"""

def init_db():
    with pg() as conn:
        with conn.cursor() as cur:
            cur.execute(SCHEMA)
    log.info("PostgreSQL initialised.")

def generate_pin() -> str:
    """Generate a random 5-digit PIN (10000-99999)."""
    return str(random.randint(10000, 99999))

def hash_pin(pin: str) -> str:
    return hashlib.sha256(f"{SECRET_SALT}{pin}".encode()).hexdigest()

PIN_MAX_FAILURES = 3
PIN_LOCKOUT_SECONDS = 3600  # 1 hour

def _pin_fail_key(uid: int) -> str:
    return f"pin_fail:{uid}"

def _pin_lock_key(uid: int) -> str:
    return f"pin_lock:{uid}"

def check_pin_lockout(uid: int) -> int:
    """Return seconds remaining in lockout, or 0 if not locked."""
    ttl = r().ttl(_pin_lock_key(uid))
    return max(ttl, 0)

def record_pin_failure(uid: int) -> int:
    """Increment failure counter. Lock account for 1h after 3 failures.
    Returns remaining attempts (0 = just locked)."""
    fail_key = _pin_fail_key(uid)
    pipe = r().pipeline()
    pipe.incr(fail_key)
    pipe.expire(fail_key, PIN_LOCKOUT_SECONDS * 2)
    results = pipe.execute()
    count = int(results[0])
    if count >= PIN_MAX_FAILURES:
        r().setex(_pin_lock_key(uid), PIN_LOCKOUT_SECONDS, "1")
        r().delete(fail_key)
        log.warning(f"PIN lockout activated for user {uid} (1 hour)")
        return 0
    remaining = PIN_MAX_FAILURES - count
    return remaining

def clear_pin_failures(uid: int):
    """Reset failure counter and lock on successful PIN entry."""
    r().delete(_pin_fail_key(uid))
    r().delete(_pin_lock_key(uid))

def blur(n: str) -> str:
    if not n: return "??"
    if len(n) <= 3: return n[0] + "**"
    return n[:2] + "*" * (len(n) - 2)

def tour_id() -> str:
    return f"T{int(time.time())}{random.randint(100,999)}"

def fmt(v) -> str:
    return f"{float(v):,.0f}"

def get_user(uid: int):
    key = f"user:{uid}"
    cached = r().get(key)
    if cached:
        return json.loads(cached)
    with pg() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM users WHERE user_id=%s", (uid,))
            row = cur.fetchone()
    if row:
        r().setex(key, 60, json.dumps(dict(row), default=str))
    return dict(row) if row else None

def invalidate_user_cache(uid: int):
    r().delete(f"user:{uid}")

def get_solde(uid: int) -> float:
    val = r().get(f"solde:{uid}")
    if val is not None:
        return float(val)
    u = get_user(uid)
    if u:
        r().set(f"solde:{uid}", str(u["solde"]))
        return float(u["solde"])
    return 0.0

def update_solde(uid: int, delta: float) -> float:
    with pg() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE users SET solde=solde+%s, last_seen=NOW() WHERE user_id=%s RETURNING solde",
                (delta, uid)
            )
            row = cur.fetchone()
    new_s = float(row[0]) if row else 0.0
    r().set(f"solde:{uid}", str(new_s))
    invalidate_user_cache(uid)
    return new_s

def create_user(uid: int, username: str, tg_name: str,
                custom_name: str, pin: str, parrain_id: int = None) -> bool:
    bonus = BONUS_BIENVENUE + (BONUS_FILLEUL if parrain_id else 0)
    try:
        with pg() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO users
                    (user_id, username, tg_name, custom_name, solde, pin_hash, parrain_id)
                    VALUES (%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT(user_id) DO NOTHING
                """, (uid, username, tg_name, custom_name, bonus, hash_pin(pin), parrain_id))
        r().set(f"solde:{uid}", str(bonus))
        invalidate_user_cache(uid)
        log.info(f"New player: {uid} ({custom_name}) parrain={parrain_id}")
        return True
    except Exception as e:
        log.error(f"create_user error: {e}")
        return False

def verify_pin(uid: int, pin: str) -> dict:
    """
    Returns dict with keys:
      ok (bool), message (str), lockout_secs (int, 0 if not locked)
    """
    # Check account ban
    u = get_user(uid)
    if not u:
        return {"ok": False, "message": "Compte introuvable.", "lockout_secs": 0}
    if u.get("is_banned"):
        return {"ok": False, "message": "Votre compte est banni.", "lockout_secs": 0}

    # Check lockout
    remaining_lock = check_pin_lockout(uid)
    if remaining_lock > 0:
        mins = remaining_lock // 60
        return {
            "ok": False,
            "message": f"Compte verrouille. Reessayez dans {mins} min.",
            "lockout_secs": remaining_lock,
        }

    # Verify PIN
    if u["pin_hash"] == hash_pin(str(pin)):
        clear_pin_failures(uid)
        return {"ok": True, "message": "OK", "lockout_secs": 0}

    # Wrong PIN — record failure
    remaining_attempts = record_pin_failure(uid)
    if remaining_attempts == 0:
        return {
            "ok": False,
            "message": "PIN incorrect. Compte verrouille 1 heure (3 echecs).",
            "lockout_secs": PIN_LOCKOUT_SECONDS,
        }
    return {
        "ok": False,
        "message": f"PIN incorrect. {remaining_attempts} tentative(s) restante(s).",
        "lockout_secs": 0,
    }

def user_exists(uid: int) -> bool:
    return get_user(uid) is not None

def update_last_seen(uid: int):
    with pg() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE users SET last_seen=NOW() WHERE user_id=%s", (uid,))
    invalidate_user_cache(uid)

def check_absence_bonus(uid: int) -> float:
    u = get_user(uid)
    if not u: return 0
    last = u.get("last_seen")
    if not last: return 0
    if isinstance(last, str):
        from dateutil import parser
        last = parser.parse(last)
    delta = datetime.now(last.tzinfo) - last
    if delta.total_seconds() >= ABSENCE_BONUS_H * 3600:
        update_solde(uid, ABSENCE_BONUS_XAF)
        log.info(f"Bonus retour {ABSENCE_BONUS_XAF} XAF -> {uid}")
        return ABSENCE_BONUS_XAF
    return 0

def get_history_full(n: int = HISTORY_LIMIT) -> list:
    cache_key = f"history_full:{n}"
    cached = r().get(cache_key)
    if cached:
        return json.loads(cached)
    with pg() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT numero, tour_id, winner_name, winner_solde,
                       total_players, total_mise,
                       to_char(drawn_at, 'HH24:MI') as heure
                FROM history
                ORDER BY drawn_at DESC LIMIT %s
            """, (n,))
            rows = cur.fetchall()
    result = [dict(r) for r in rows]
    r().setex(cache_key, 10, json.dumps(result, default=str))
    return result

def get_history_nums(n: int = HISTORY_LIMIT) -> list:
    return [h["numero"] for h in get_history_full(n)]

def add_history(numero: int, tid: str, winner_uid: int = None,
                winner_name: str = None, winner_solde: float = None,
                total_players: int = 0, total_mise: float = 0):
    with pg() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO history
                (numero, tour_id, winner_uid, winner_name, winner_solde, total_players, total_mise)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
            """, (numero, tid, winner_uid, winner_name, winner_solde, total_players, total_mise))
    for n in [10, 20]:
        r().delete(f"history_full:{n}")
    log.info(f"History: N{numero} | winner={winner_name} | solde={winner_solde}")

def fill_history_if_empty():
    nums = get_history_nums(20)
    if len(nums) >= 10:
        return
    FICTIFS = [
        ("SN","Moussa"), ("FR","Emma"), ("GH","Kofi"),
        ("US","Jackson"), ("CM","Ines"), ("BR","Rafael"),
        ("DE","Hanna"), ("CI","Yao"), ("JP","Kenji"),
        ("NG","Ngozi"), ("ES","Mateo"), ("RU","Ivan"),
    ]
    missing = 10 - len(nums)
    for i in range(missing):
        flag, nom = FICTIFS[i % len(FICTIFS)]
        solde_f = round(random.uniform(300, 6000), 0)
        add_history(
            numero=random.randint(0,5),
            tid=tour_id(),
            winner_name=f"{flag}-{blur(nom)}",
            winner_solde=solde_f,
            total_players=random.randint(8, 30),
            total_mise=round(random.uniform(200, 4000), 0)
        )
    log.info(f"{missing} seed history entries inserted.")

def enregistrer_filleul(parrain_id: int, filleul_id: int) -> bool:
    if parrain_id == filleul_id: return False
    if not user_exists(parrain_id): return False
    try:
        with pg() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM parrainage WHERE filleul_id=%s", (filleul_id,))
                if cur.fetchone(): return False
                cur.execute(
                    "INSERT INTO parrainage (parrain_id, filleul_id) VALUES (%s,%s)",
                    (parrain_id, filleul_id)
                )
                cur.execute(
                    "UPDATE users SET filleuls_cnt=filleuls_cnt+1 WHERE user_id=%s",
                    (parrain_id,)
                )
        update_solde(parrain_id, BONUS_PARRAIN)
        invalidate_user_cache(parrain_id)
        log.info(f"Parrainage: {parrain_id} -> {filleul_id}")
        return True
    except Exception as e:
        log.error(f"parrainage error: {e}")
        return False

def get_stats_parrain(uid: int) -> dict:
    with pg() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT COUNT(*) as nb,
                       COALESCE(SUM(CASE WHEN bonus_verse THEN 1 ELSE 0 END),0) as payes
                FROM parrainage WHERE parrain_id=%s
            """, (uid,))
            row = cur.fetchone()
    nb = int(row["nb"]) if row else 0
    return {
        "filleuls": nb,
        "bonus_total": nb * BONUS_PARRAIN,
        "lien": f"https://t.me/NkapExpressBot?start=ref_{uid}"
    }

def insert_bet(uid: int, tid: str, numero: int, mise: float) -> int:
    with pg() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO bets (user_id, tour_id, numero, mise)
                VALUES (%s,%s,%s,%s) RETURNING id
            """, (uid, tid, numero, mise))
            bid = cur.fetchone()[0]
            cur.execute("UPDATE users SET total_mises=total_mises+1 WHERE user_id=%s", (uid,))
    return bid

def resolve_bet(bid: int, won: bool, gain: float, uid: int):
    statut = "GAGNE" if won else "PERDU"
    with pg() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE bets SET statut=%s, gain=%s WHERE id=%s", (statut, gain, bid))
            if won:
                cur.execute("""
                    UPDATE users
                    SET total_gains=total_gains+%s,
                        meilleur_gain=GREATEST(meilleur_gain,%s)
                    WHERE user_id=%s
                """, (gain, gain, uid))
                cur.execute("""
                    INSERT INTO leaderboard_daily(user_id, gains_jour, date_jour)
                    VALUES(%s,%s,CURRENT_DATE)
                    ON CONFLICT(user_id,date_jour)
                    DO UPDATE SET gains_jour=leaderboard_daily.gains_jour+%s
                """, (uid, gain, gain))

def get_leaderboard(limit: int = 10) -> list:
    cache_key = "leaderboard:daily"
    cached = r().get(cache_key)
    if cached:
        return json.loads(cached)
    with pg() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT u.custom_name, u.tg_name, l.gains_jour
                FROM leaderboard_daily l
                JOIN users u ON u.user_id=l.user_id
                WHERE l.date_jour=CURRENT_DATE
                ORDER BY l.gains_jour DESC LIMIT %s
            """, (limit,))
            rows = cur.fetchall()
    result = [dict(r) for r in rows]
    r().setex(cache_key, 30, json.dumps(result))
    return result

def get_server_state() -> dict:
    cached = r().get("server_state")
    if cached:
        return json.loads(cached)
    with pg() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM server_state WHERE id=1")
            row = cur.fetchone()
    result = dict(row) if row else {"is_open": False, "open_key": ""}
    r().setex("server_state", 5, json.dumps(result, default=str))
    return result

def set_server_open(is_open: bool, key: str = ""):
    with pg() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE server_state
                SET is_open=%s, open_at=%s, open_key=%s
                WHERE id=1
            """, (is_open, datetime.now() if is_open else None, key))
    r().delete("server_state")

def check_rate_limit(uid: int, action: str, max_per_min: int = 20) -> bool:
    key = f"rl:{uid}:{action}"
    pipe = r().pipeline()
    pipe.incr(key)
    pipe.expire(key, 60)
    results = pipe.execute()
    count = results[0]
    return count <= max_per_min

def get_admin_stats() -> dict:
    with pg() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT COUNT(*) as n FROM users")
            nb_users = cur.fetchone()["n"]
            cur.execute("SELECT COALESCE(SUM(solde),0) as s FROM users")
            total_soldes = float(cur.fetchone()["s"])
            cur.execute("SELECT COUNT(*) as n FROM bets WHERE placed_at > NOW()-INTERVAL '24h'")
            bets_24h = cur.fetchone()["n"]
            cur.execute("SELECT COUNT(*) as n FROM parrainage")
            parrainages = cur.fetchone()["n"]
            cur.execute("SELECT COUNT(*) as n FROM depot_demandes WHERE statut='EN_ATTENTE'")
            depots_attente = cur.fetchone()["n"]
            cur.execute("SELECT COUNT(*) as n FROM retrait_demandes WHERE statut='EN_ATTENTE'")
            retraits_attente = cur.fetchone()["n"]
    return {
        "nb_users": nb_users,
        "total_soldes": total_soldes,
        "bets_24h": bets_24h,
        "parrainages": parrainages,
        "depots_attente": depots_attente,
        "retraits_attente": retraits_attente,
    }
