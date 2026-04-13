"""
config.py — NKAP EXPRESS
Configuration centralisée — toutes les variables d'environnement Railway
"""
import os

# ── Telegram ──────────────────────────────────────────────
BOT_TOKEN      = os.environ.get("BOT_TOKEN", "")
WEBAPP_URL     = os.environ.get("WEBAPP_URL", "")
WEBHOOK_URL    = os.environ.get("WEBHOOK_URL", "")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "NKAP_WEBHOOK_SECRET_2025")

# ── PostgreSQL (Railway injecte DATABASE_URL automatiquement) ──
DATABASE_URL = os.environ.get("DATABASE_URL", "")
PG_HOST      = os.environ.get("PGHOST",     "localhost")
PG_PORT      = int(os.environ.get("PGPORT", "5432"))
PG_DB        = os.environ.get("PGDATABASE", "nkap_db")
PG_USER      = os.environ.get("PGUSER",     "postgres")
PG_PASS      = os.environ.get("PGPASSWORD", "")

# ── Redis ──────────────────────────────────────────────────
REDIS_URL  = os.environ.get("REDIS_URL", "")
REDIS_HOST = os.environ.get("REDIS_HOST", "localhost")
REDIS_PORT = int(os.environ.get("REDIS_PORT", "6379"))
REDIS_DB   = int(os.environ.get("REDIS_DB",   "0"))

# ── Sécurité ───────────────────────────────────────────────
SECRET_SALT = os.environ.get("SECRET_SALT", "NKAP_SALT_2025_ULTRA")
ADMIN_IDS   = [int(x) for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip()]

# ── Serveur Flask ──────────────────────────────────────────
FLASK_PORT     = int(os.environ.get("PORT", os.environ.get("FLASK_PORT", "8080")))
RATE_LIMIT_MAX = int(os.environ.get("RATE_LIMIT_MAX", "20"))

# ── Paramètres du Jeu ──────────────────────────────────────
PHASE_BETS     = int(os.environ.get("PHASE_BETS",    "15"))   # secondes phase mise
PHASE_RESULT   = int(os.environ.get("PHASE_RESULT",  "8"))    # secondes phase résultat/podium
WIN_MULTIPLIER = int(os.environ.get("WIN_MULTIPLIER","5"))    # x5 la mise

# ── Bonus & Économie ───────────────────────────────────────
BONUS_BIENVENUE = int(os.environ.get("BONUS_BIENVENUE", "200"))
BONUS_PARRAIN   = int(os.environ.get("BONUS_PARRAIN",   "250"))
BONUS_FILLEUL   = int(os.environ.get("BONUS_FILLEUL",   "100"))
HISTORY_LIMIT   = int(os.environ.get("HISTORY_LIMIT",   "20"))
MAX_MISE        = int(os.environ.get("MAX_MISE",         "1000"))
MIN_MISE        = int(os.environ.get("MIN_MISE",         "1"))
MIN_DEPOT       = int(os.environ.get("MIN_DEPOT",        "100"))
MIN_RETRAIT     = int(os.environ.get("MIN_RETRAIT",      "100"))

# ── Bonus d'Absence ───────────────────────────────────────
ABSENCE_BONUS_H   = int(os.environ.get("ABSENCE_BONUS_H",   "24"))
ABSENCE_BONUS_XAF = int(os.environ.get("ABSENCE_BONUS_XAF", "50"))

# ── Honey Pot (50 premiers joueurs) ───────────────────────
HONEYPOT_THRESHOLD = int(os.environ.get("HONEYPOT_THRESHOLD", "50"))
HONEYPOT_CYCLE     = int(os.environ.get("HONEYPOT_CYCLE",     "6"))

# ── Predictor (actif après PREDICTOR_MIN_USERS joueurs) ───
PREDICTOR_MIN_USERS = int(os.environ.get("PREDICTOR_MIN_USERS", "50"))
PREDICTOR_GUIDE_PRICE    = int(os.environ.get("PREDICTOR_GUIDE_PRICE",    "200"))
PREDICTOR_EXPERT_PRICE   = int(os.environ.get("PREDICTOR_EXPERT_PRICE",   "10000"))
PREDICTOR_IMPERIAL_PRICE = int(os.environ.get("PREDICTOR_IMPERIAL_PRICE", "100000"))

# ── Caisse / Predato ──────────────────────────────────────
CAISSE_INIT          = int(os.environ.get("CAISSE_INIT",          "500000"))
PREDATO_THRESHOLD    = float(os.environ.get("PREDATO_THRESHOLD",  "0.50"))   # 50% de la caisse
PREDATO_MIN_USERS    = int(os.environ.get("PREDATO_MIN_USERS",    "50"))

# ── Rapport quotidien (heure locale du serveur) ───────────
RAPPORT_HEURE = int(os.environ.get("RAPPORT_HEURE", "23"))   # 23h00
