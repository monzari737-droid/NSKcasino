import os

BOT_TOKEN      = os.environ.get("BOT_TOKEN", "")
WEBAPP_URL     = os.environ.get("WEBAPP_URL", "")
WEBHOOK_URL    = os.environ.get("WEBHOOK_URL", "")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "NKAP_WEBHOOK_SECRET_2025")

PG_DSN = os.environ.get("DATABASE_URL", "")
PG_HOST = os.environ.get("PGHOST", "localhost")
PG_PORT = int(os.environ.get("PGPORT", "5432"))
PG_DB   = os.environ.get("PGDATABASE", "nkap_db")
PG_USER = os.environ.get("PGUSER", "postgres")
PG_PASS = os.environ.get("PGPASSWORD", "")

REDIS_HOST = os.environ.get("REDIS_HOST", "localhost")
REDIS_PORT = int(os.environ.get("REDIS_PORT", "6379"))
REDIS_DB   = int(os.environ.get("REDIS_DB", "0"))

SECRET_SALT    = os.environ.get("SECRET_SALT", "NKAP_SALT_2025_ULTRA")
ADMIN_IDS      = [int(x) for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip()]
FLASK_PORT     = int(os.environ.get("FLASK_PORT", "8080"))
RATE_LIMIT_MAX = int(os.environ.get("RATE_LIMIT_MAX", "20"))

PHASE_BETS      = int(os.environ.get("PHASE_BETS", "15"))
PHASE_RESULT    = int(os.environ.get("PHASE_RESULT", "4"))
WIN_MULTIPLIER  = int(os.environ.get("WIN_MULTIPLIER", "5"))
BONUS_BIENVENUE = int(os.environ.get("BONUS_BIENVENUE", "200"))
BONUS_PARRAIN   = int(os.environ.get("BONUS_PARRAIN", "250"))
BONUS_FILLEUL   = int(os.environ.get("BONUS_FILLEUL", "100"))
HISTORY_LIMIT   = int(os.environ.get("HISTORY_LIMIT", "20"))
MAX_MISE        = int(os.environ.get("MAX_MISE", "1000"))
MIN_MISE        = int(os.environ.get("MIN_MISE", "1"))
MIN_DEPOT       = int(os.environ.get("MIN_DEPOT", "100"))
MIN_RETRAIT     = int(os.environ.get("MIN_RETRAIT", "100"))

ABSENCE_BONUS_H   = int(os.environ.get("ABSENCE_BONUS_H", "24"))
ABSENCE_BONUS_XAF = int(os.environ.get("ABSENCE_BONUS_XAF", "50"))
