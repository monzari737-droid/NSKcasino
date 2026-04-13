#!/usr/bin/env python3
import hashlib, logging, threading, time, json
from datetime import datetime
from flask import Flask, request as freq, jsonify, abort
import telebot
from telebot.types import (
    ReplyKeyboardMarkup, KeyboardButton, WebAppInfo,
    InlineKeyboardMarkup, InlineKeyboardButton
)

from config import (
    BOT_TOKEN, WEBAPP_URL, WEBHOOK_URL, WEBHOOK_SECRET,
    FLASK_PORT, ADMIN_IDS, RATE_LIMIT_MAX,
    BONUS_BIENVENUE, BONUS_PARRAIN, BONUS_FILLEUL,
    PHASE_BETS, WIN_MULTIPLIER
)
from database import (
    init_db, fill_history_if_empty,
    get_user, get_solde, update_solde,
    create_user, verify_pin, user_exists,
    update_last_seen, check_absence_bonus,
    get_history_full, get_history_nums,
    get_stats_parrain, enregistrer_filleul,
    get_server_state, set_server_open,
    get_admin_stats, get_leaderboard,
    check_rate_limit, invalidate_user_cache,
    generate_pin, check_pin_lockout,
    blur, fmt, pg
)
from engine import engine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
log = logging.getLogger("NkapBot")

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML", threaded=True, num_threads=8)
app = Flask(__name__)

def notify(uid: int, msg: str):
    try: bot.send_message(uid, msg)
    except Exception as e: log.warning(f"notify {uid}: {e}")

engine.set_notify_callback(notify)

user_states: dict = {}

def get_st(uid):       return user_states.get(uid, {})
def set_st(uid, s, **d): user_states[uid] = {"state": s, "data": d}
def clear_st(uid):     user_states.pop(uid, None)
def is_admin(uid):     return uid in ADMIN_IDS

def main_kb(uid):
    kb = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    if WEBAPP_URL:
        kb.add(KeyboardButton("Jouer NKAP EXPRESS", web_app=WebAppInfo(url=WEBAPP_URL)))
    kb.add(KeyboardButton("Mon Solde"),    KeyboardButton("Historique"))
    kb.add(KeyboardButton("Deposer"),      KeyboardButton("Retirer"))
    kb.add(KeyboardButton("Parrainage"),   KeyboardButton("Classement"))
    kb.add(KeyboardButton("Mon Profil"),   KeyboardButton("Aide"))
    if is_admin(uid):
        kb.add(KeyboardButton("ADMIN PANEL"))
    return kb

def admin_panel_kb():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("Ouvrir le site",      callback_data="adm:open:0"),
        InlineKeyboardButton("Ouvrir dans 30s",     callback_data="adm:open:30"),
    )
    kb.add(
        InlineKeyboardButton("Ouvrir dans 60s",     callback_data="adm:open:60"),
        InlineKeyboardButton("Fermer le site",      callback_data="adm:close"),
    )
    kb.add(
        InlineKeyboardButton("Statistiques",        callback_data="adm:stats"),
        InlineKeyboardButton("Classement du jour",  callback_data="adm:lb"),
    )
    kb.add(
        InlineKeyboardButton("Depots en attente",   callback_data="adm:depots"),
        InlineKeyboardButton("Retraits en attente", callback_data="adm:retraits"),
    )
    kb.add(
        InlineKeyboardButton("Crediter un joueur",  callback_data="adm:credit"),
        InlineKeyboardButton("Chercher un joueur",  callback_data="adm:search"),
    )
    kb.add(
        InlineKeyboardButton("Bannir un joueur",    callback_data="adm:ban"),
        InlineKeyboardButton("Debannir un joueur",  callback_data="adm:unban"),
    )
    kb.add(
        InlineKeyboardButton("Message a tous",      callback_data="adm:broadcast"),
    )
    kb.add(
        InlineKeyboardButton("Etat du moteur",      callback_data="adm:engine"),
        InlineKeyboardButton("20 derniers tirages", callback_data="adm:history"),
    )
    kb.add(
        InlineKeyboardButton("Rafraichir",          callback_data="adm:refresh"),
    )
    return kb

def send_admin_panel(uid, edit_msg=None):
    srv   = get_server_state()
    stats = get_admin_stats()
    status = "OUVERT" if srv.get("is_open") else "FERME"
    phase  = engine.phase.upper()
    text = (
        f"<b>PANNEAU ADMIN -- NKAP EXPRESS</b>\n"
        f"{'='*32}\n\n"
        f"Site : <b>{status}</b>\n"
        f"Phase jeu : <b>{phase}</b>\n"
        f"Joueurs inscrits : <b>{stats['nb_users']}</b>\n"
        f"Soldes totaux : <b>{stats['total_soldes']:.0f} XAF</b>\n"
        f"Mises (24h) : <b>{stats['bets_24h']}</b>\n"
        f"Depots en attente : <b>{stats['depots_attente']}</b>\n"
        f"Retraits en attente : <b>{stats['retraits_attente']}</b>\n"
        f"Parrainages : <b>{stats['parrainages']}</b>\n\n"
        f"<i>Derniere MaJ : {datetime.now().strftime('%H:%M:%S')}</i>"
    )
    if edit_msg:
        try:
            bot.edit_message_text(
                text, edit_msg.chat.id, edit_msg.message_id,
                reply_markup=admin_panel_kb(), parse_mode="HTML"
            )
        except: pass
    else:
        bot.send_message(uid, text, reply_markup=admin_panel_kb())

NC_EMOJI = ["0","1","2","3","4","5"]

def fmt_history_bot(hist: list) -> str:
    if not hist:
        return "Aucun tirage enregistre."
    lines = []
    for h in hist[:20]:
        n    = h["numero"]
        nom  = h.get("winner_name") or "--"
        sol  = f"{float(h['winner_solde']):.0f} XAF" if h.get("winner_solde") else "--"
        heur = h.get("heure","")
        lines.append(f"<b>N{n}</b>  {nom}  <i>{sol}</i>  <code>{heur}</code>")
    return "\n".join(lines)

def parse_ref(text: str):
    if text and text.startswith("ref_"):
        try: return int(text[4:])
        except: return None
    return None

def get_user_info_text(uid_or_username: str) -> str:
    try: search_id = int(uid_or_username)
    except: search_id = None
    import psycopg2.extras
    with pg() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if search_id:
                cur.execute("SELECT * FROM users WHERE user_id=%s", (search_id,))
            else:
                cur.execute(
                    "SELECT * FROM users WHERE username ILIKE %s OR custom_name ILIKE %s",
                    (f"%{uid_or_username}%", f"%{uid_or_username}%")
                )
            u = cur.fetchone()
    if not u:
        return "Joueur introuvable."
    banned = "OUI" if u.get("is_banned") else "NON"
    return (
        f"<b>Fiche Joueur</b>\n\n"
        f"ID : <code>{u['user_id']}</code>\n"
        f"Nom : <b>{u.get('custom_name') or u.get('tg_name','?')}</b>\n"
        f"@{u.get('username') or '--'}\n"
        f"Solde : <b>{float(u.get('solde',0)):.0f} XAF</b>\n"
        f"Mises : <b>{u.get('total_mises',0)}</b>\n"
        f"Gains totaux : <b>{float(u.get('total_gains',0)):.0f} XAF</b>\n"
        f"Meilleur gain : <b>{float(u.get('meilleur_gain',0)):.0f} XAF</b>\n"
        f"Banni : {banned}\n"
        f"Inscrit le : {str(u.get('created_at',''))[:10]}\n"
        f"Derniere connexion : {str(u.get('last_seen',''))[:16]}"
    )

@bot.callback_query_handler(func=lambda c: c.data.startswith("adm:"))
def admin_callback(call):
    uid = call.from_user.id
    if not is_admin(uid):
        bot.answer_callback_query(call.id, "Acces refuse.")
        return

    parts  = call.data.split(":")
    action = parts[1] if len(parts) > 1 else ""
    param  = parts[2] if len(parts) > 2 else ""

    if action == "open":
        delay = int(param) if param.isdigit() else 0
        key   = hashlib.sha256(f"OPEN_{time.time()}".encode()).hexdigest()[:16]
        if delay > 0:
            def _countdown():
                set_server_open(False, "")
                from database import r
                for i in range(delay, 0, -1):
                    r().set("server_countdown", i, ex=delay+5)
                    time.sleep(1)
                set_server_open(True, key)
                r().delete("server_countdown")
                bot.send_message(uid, f"Site ouvert automatiquement !")
            threading.Thread(target=_countdown, daemon=True).start()
            bot.answer_callback_query(call.id, f"Ouverture dans {delay}s...")
            bot.send_message(uid, f"Compteur lance : <b>{delay} secondes</b>\nCle : <code>{key}</code>")
        else:
            set_server_open(True, key)
            bot.answer_callback_query(call.id, "Site ouvert !")
            bot.send_message(uid, f"Site <b>ouvert</b> immediatement.\nCle : <code>{key}</code>")
        send_admin_panel(uid, call.message)

    elif action == "close":
        set_server_open(False, "")
        bot.answer_callback_query(call.id, "Site ferme.")
        send_admin_panel(uid, call.message)

    elif action == "stats":
        bot.answer_callback_query(call.id, "Chargement...")
        send_admin_panel(uid, call.message)

    elif action == "refresh":
        bot.answer_callback_query(call.id, "Rafraichi !")
        send_admin_panel(uid, call.message)

    elif action == "lb":
        bot.answer_callback_query(call.id)
        lb = get_leaderboard(10)
        if not lb:
            bot.send_message(uid, "Aucun gagnant aujourd'hui."); return
        medals = ["1.","2.","3.","4.","5.","6.","7.","8.","9.","10."]
        lines  = []
        for i, p in enumerate(lb):
            nom = p.get("custom_name") or p.get("tg_name","?")
            lines.append(f"{medals[i]} <b>{nom}</b> -- {float(p['gains_jour']):.0f} XAF")
        bot.send_message(uid, "<b>Top 10 du Jour</b>\n\n" + "\n".join(lines))

    elif action == "depots":
        bot.answer_callback_query(call.id)
        with pg() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT d.id, d.user_id, u.custom_name, d.montant, d.telephone, d.created_at
                    FROM depot_demandes d
                    LEFT JOIN users u ON u.user_id=d.user_id
                    WHERE d.statut='EN_ATTENTE'
                    ORDER BY d.created_at DESC LIMIT 10
                """)
                rows = cur.fetchall()
        if not rows:
            bot.send_message(uid, "Aucun depot en attente."); return
        for row in rows:
            rid, tuid, nom, mont, tel, dt = row
            kb2 = InlineKeyboardMarkup()
            kb2.add(
                InlineKeyboardButton(
                    f"Valider {mont:.0f} XAF",
                    callback_data=f"adm:valider_depot:{rid}:{tuid}:{mont}"
                ),
                InlineKeyboardButton(
                    "Rejeter",
                    callback_data=f"adm:rejeter_depot:{rid}:{tuid}"
                )
            )
            bot.send_message(uid,
                f"<b>Depot #{rid}</b>\n"
                f"{nom or '?'} (<code>{tuid}</code>)\n"
                f"Montant : <b>{mont:.0f} XAF</b>\n"
                f"Numero : <b>{tel}</b>\n"
                f"{str(dt)[:16]}",
                reply_markup=kb2)

    elif action == "valider_depot":
        try:
            rid   = int(parts[2])
            tuid  = int(parts[3])
            mont  = float(parts[4])
        except:
            bot.answer_callback_query(call.id, "Erreur parametres"); return
        new_s = update_solde(tuid, mont)
        with pg() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE depot_demandes SET statut='VALIDE', validated_at=NOW() WHERE id=%s",
                    (rid,)
                )
        bot.answer_callback_query(call.id, f"+{mont:.0f} XAF credite !")
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
        bot.send_message(uid, f"Depot #{rid} valide. Solde {tuid} : <b>{new_s:.0f} XAF</b>")
        try:
            bot.send_message(tuid,
                f"<b>Depot confirme !</b>\n\n"
                f"<b>+{mont:.0f} XAF</b> credites.\n"
                f"Nouveau solde : <b>{new_s:.0f} XAF</b>\n"
                f"Bonne chance sur NKAP EXPRESS !")
        except: pass

    elif action == "rejeter_depot":
        try:
            rid  = int(parts[2])
            tuid = int(parts[3])
        except:
            bot.answer_callback_query(call.id, "Erreur"); return
        with pg() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE depot_demandes SET statut='REJETE' WHERE id=%s", (rid,)
                )
        bot.answer_callback_query(call.id, "Depot rejete.")
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
        try:
            bot.send_message(tuid,
                f"<b>Depot refuse</b>\n\n"
                f"Votre depot #{rid} n'a pas pu etre verifie.\n"
                f"Contactez le support pour plus d'informations.")
        except: pass

    elif action == "retraits":
        bot.answer_callback_query(call.id)
        with pg() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT r.id, r.user_id, u.custom_name, r.montant, r.telephone, r.created_at
                    FROM retrait_demandes r
                    LEFT JOIN users u ON u.user_id=r.user_id
                    WHERE r.statut='EN_ATTENTE'
                    ORDER BY r.created_at DESC LIMIT 10
                """)
                rows = cur.fetchall()
        if not rows:
            bot.send_message(uid, "Aucun retrait en attente."); return
        for row in rows:
            rid, tuid, nom, mont, tel, dt = row
            kb2 = InlineKeyboardMarkup()
            kb2.add(
                InlineKeyboardButton(
                    "Confirmer envoi",
                    callback_data=f"adm:valider_retrait:{rid}:{tuid}"
                ),
                InlineKeyboardButton(
                    "Annuler (rembourser)",
                    callback_data=f"adm:annuler_retrait:{rid}:{tuid}:{mont}"
                )
            )
            bot.send_message(uid,
                f"<b>Retrait #{rid}</b>\n"
                f"{nom or '?'} (<code>{tuid}</code>)\n"
                f"Montant : <b>{mont:.0f} XAF</b>\n"
                f"Envoyer a : <b>{tel}</b>\n"
                f"{str(dt)[:16]}",
                reply_markup=kb2)

    elif action == "valider_retrait":
        try: rid=int(parts[2]); tuid=int(parts[3])
        except: bot.answer_callback_query(call.id,"Erreur"); return
        with pg() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE retrait_demandes SET statut='TRAITE', validated_at=NOW() WHERE id=%s",
                    (rid,)
                )
        bot.answer_callback_query(call.id,"Retrait confirme.")
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
        try:
            bot.send_message(tuid,
                f"<b>Retrait traite !</b>\n\n"
                f"Votre argent a ete envoye sur votre Mobile Money.\n"
                f"Si vous ne le recevez pas dans 30 min, contactez le support.")
        except: pass

    elif action == "annuler_retrait":
        try: rid=int(parts[2]); tuid=int(parts[3]); mont=float(parts[4])
        except: bot.answer_callback_query(call.id,"Erreur"); return
        new_s = update_solde(tuid, mont)
        with pg() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE retrait_demandes SET statut='ANNULE' WHERE id=%s", (rid,)
                )
        bot.answer_callback_query(call.id, f"{mont:.0f} XAF rembourse.")
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
        try:
            bot.send_message(tuid,
                f"<b>Retrait annule</b>\n\n"
                f"<b>+{mont:.0f} XAF</b> rembourses sur votre compte.\n"
                f"Nouveau solde : <b>{new_s:.0f} XAF</b>")
        except: pass

    elif action == "credit":
        bot.answer_callback_query(call.id)
        set_st(uid, "ADMIN_CREDIT")
        bot.send_message(uid,
            "<b>Crediter un joueur</b>\n\n"
            "Envoyez : <code>ID_JOUEUR MONTANT [raison]</code>\n"
            "Exemple : <code>123456789 500 Bonus tournoi</code>\n\n"
            "Tapez /annuler pour annuler.")

    elif action == "search":
        bot.answer_callback_query(call.id)
        set_st(uid, "ADMIN_SEARCH")
        bot.send_message(uid,
            "<b>Chercher un joueur</b>\n\n"
            "Entrez l'ID Telegram, le @username ou le nom du joueur :\n\n"
            "Tapez /annuler pour annuler.")

    elif action == "ban":
        bot.answer_callback_query(call.id)
        set_st(uid, "ADMIN_BAN")
        bot.send_message(uid,
            "<b>Bannir un joueur</b>\n\n"
            "Entrez l'ID Telegram du joueur :\n\n"
            "Tapez /annuler pour annuler.")

    elif action == "unban":
        bot.answer_callback_query(call.id)
        set_st(uid, "ADMIN_UNBAN")
        bot.send_message(uid,
            "<b>Debannir un joueur</b>\n\n"
            "Entrez l'ID Telegram du joueur :\n\n"
            "Tapez /annuler pour annuler.")

    elif action == "broadcast":
        bot.answer_callback_query(call.id)
        set_st(uid, "ADMIN_BROADCAST")
        bot.send_message(uid,
            "<b>Message a tous les joueurs</b>\n\n"
            "Ecrivez votre message (HTML autorise) :\n\n"
            "Tapez /annuler pour annuler.")

    elif action == "engine":
        bot.answer_callback_query(call.id)
        state = engine.get_state()
        bot.send_message(uid,
            f"<b>Etat du Moteur de Jeu</b>\n\n"
            f"Phase : <b>{state.get('phase','?').upper()}</b>\n"
            f"Tour ID : <code>{state.get('tour_id','?')}</code>\n"
            f"Joueurs ce tour : <b>{state.get('total_players',0)}</b>\n"
            f"Mises reelles : <b>{state.get('real_count',0)}</b>\n"
            f"Bots actifs : <b>{len(state.get('bots',[]))}</b>\n"
            f"N gagnant : <b>{state.get('win_number','En cours...')}</b>")

    elif action == "history":
        bot.answer_callback_query(call.id)
        hist = get_history_full(20)
        bot.send_message(uid,
            f"<b>20 Derniers Tirages</b>\n\n"
            + fmt_history_bot(hist) +
            "\n\n<i>Source : PostgreSQL (identique au site)</i>")


@bot.message_handler(commands=["start"])
def cmd_start(msg):
    uid   = msg.from_user.id
    args  = msg.text.split(maxsplit=1)[1].strip() if len(msg.text.split())>1 else ""
    parrain = parse_ref(args)

    if not check_rate_limit(uid, "start", 5): return

    u = get_user(uid)
    if u:
        bonus_retour = check_absence_bonus(uid)
        update_last_seen(uid)
        solde = get_solde(uid)
        txt = (
            f"Bon retour <b>{u['custom_name'] or u['tg_name']}</b> !\n\n"
            f"Solde : <b>{solde:.0f} XAF</b>\n"
        )
        if bonus_retour > 0:
            txt += f"Bonus retour : <b>+{bonus_retour:.0f} XAF</b>\n"
        txt += "\nAppuyez sur <b>Jouer</b> pour ouvrir le casino."
        bot.send_message(uid, txt, reply_markup=main_kb(uid))
        if parrain and parrain != uid:
            enregistrer_filleul(parrain, uid)
    else:
        set_st(uid, "ASK_NAME", parrain_id=parrain or 0)
        txt = f"<b>Bienvenue sur NKAP EXPRESS !</b>\n\nBonus bienvenue : <b>{BONUS_BIENVENUE} XAF</b>\n"
        if parrain:
            txt += f"Parraine ! Bonus supplementaire : <b>+{BONUS_FILLEUL} XAF</b>\n"
        txt += "\n<b>Quel est votre nom de joueur ?</b>\n(Ce nom sera visible dans le classement)"
        bot.send_message(uid, txt)


@bot.message_handler(commands=["annuler"])
def cmd_annuler(msg):
    uid = msg.from_user.id
    st  = get_st(uid).get("state","")
    if st.startswith("ADMIN_"):
        clear_st(uid)
        send_admin_panel(uid)
    else:
        clear_st(uid)
        bot.send_message(uid, "Action annulee.", reply_markup=main_kb(uid))


@bot.message_handler(commands=["admin"])
def cmd_admin(msg):
    if not is_admin(msg.from_user.id):
        bot.send_message(msg.from_user.id,"Acces refuse."); return
    send_admin_panel(msg.from_user.id)

@bot.message_handler(commands=["ouvrir"])
def cmd_ouvrir(msg):
    if not is_admin(msg.from_user.id): return
    parts = msg.text.split()
    delay = int(parts[1]) if len(parts)>1 and parts[1].isdigit() else 0
    key   = hashlib.sha256(f"OPEN_{time.time()}".encode()).hexdigest()[:16]
    if delay > 0:
        def _cd():
            set_server_open(False,"")
            from database import r as rc
            for i in range(delay,0,-1):
                rc().set("server_countdown",i,ex=delay+5)
                time.sleep(1)
            set_server_open(True,key)
            rc().delete("server_countdown")
        threading.Thread(target=_cd,daemon=True).start()
        bot.send_message(msg.from_user.id,f"Ouverture dans <b>{delay}s</b>. Cle : <code>{key}</code>")
    else:
        set_server_open(True,key)
        bot.send_message(msg.from_user.id,f"Site <b>ouvert</b>. Cle : <code>{key}</code>")

@bot.message_handler(commands=["fermer"])
def cmd_fermer(msg):
    if not is_admin(msg.from_user.id): return
    set_server_open(False,"")
    bot.send_message(msg.from_user.id,"Site <b>ferme</b>.")

@bot.message_handler(commands=["valider"])
def cmd_valider(msg):
    if not is_admin(msg.from_user.id): return
    parts = msg.text.split()
    if len(parts)<3:
        bot.send_message(msg.from_user.id,"Usage: /valider <user_id> <montant>"); return
    try: tuid=int(parts[1]); montant=float(parts[2])
    except: bot.send_message(msg.from_user.id,"Parametres invalides."); return
    if not get_user(tuid):
        bot.send_message(msg.from_user.id,"Utilisateur introuvable."); return
    new_s = update_solde(tuid, montant)
    bot.send_message(msg.from_user.id,
        f"<b>+{montant:.0f} XAF</b> credite a {tuid}.\nSolde : <b>{new_s:.0f} XAF</b>")
    try:
        bot.send_message(tuid,
            f"<b>Depot confirme !</b>\n+<b>{montant:.0f} XAF</b>\nSolde : <b>{new_s:.0f} XAF</b>")
    except: pass

@bot.message_handler(commands=["stats"])
def cmd_stats(msg):
    if not is_admin(msg.from_user.id): return
    send_admin_panel(msg.from_user.id)

@bot.message_handler(commands=["ban"])
def cmd_ban(msg):
    if not is_admin(msg.from_user.id): return
    parts = msg.text.split()
    if len(parts)<2: return
    try: tuid=int(parts[1])
    except: return
    with pg() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE users SET is_banned=TRUE WHERE user_id=%s",(tuid,))
    invalidate_user_cache(tuid)
    bot.send_message(msg.from_user.id,f"Utilisateur {tuid} banni.")

@bot.message_handler(commands=["retirer"])
def cmd_retirer(msg):
    uid   = msg.from_user.id
    parts = msg.text.split()
    if len(parts)!=3:
        bot.send_message(uid,"Usage: <code>/retirer MONTANT NUMERO</code>"); return
    try: montant=float(parts[1])
    except: bot.send_message(uid,"Montant invalide."); return
    tel = parts[2]
    from config import MIN_RETRAIT
    if montant<MIN_RETRAIT:
        bot.send_message(uid,f"Minimum {MIN_RETRAIT} XAF."); return
    solde=get_solde(uid)
    if solde<montant:
        bot.send_message(uid,f"Solde insuffisant ({solde:.0f} XAF)."); return
    new_s=update_solde(uid,-montant)
    with pg() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO retrait_demandes(user_id,montant,telephone) VALUES(%s,%s,%s)",
                        (uid,montant,tel))
    bot.send_message(uid,
        f"<b>Retrait en cours</b>\nMontant : <b>{montant:.0f} XAF</b>\n"
        f"Numero : <b>{tel}</b>\nSolde restant : <b>{new_s:.0f} XAF</b>")


@bot.message_handler(func=lambda m: True)
def handle_text(msg):
    uid  = msg.from_user.id
    text = (msg.text or "").strip()
    st   = get_st(uid).get("state","")

    if not check_rate_limit(uid,"msg",RATE_LIMIT_MAX):
        bot.send_message(uid,"Trop de requetes. Attendez."); return

    if st == "ADMIN_CREDIT" and is_admin(uid):
        if text == "/annuler":
            clear_st(uid); send_admin_panel(uid); return
        parts = text.split(maxsplit=2)
        if len(parts)<2:
            bot.send_message(uid,"Format : <code>ID_JOUEUR MONTANT [raison]</code>"); return
        try: tuid=int(parts[0]); mont=float(parts[1])
        except: bot.send_message(uid,"ID ou montant invalide."); return
        raison = parts[2] if len(parts)>2 else "Credit admin"
        if not get_user(tuid):
            bot.send_message(uid,"Joueur introuvable."); return
        new_s = update_solde(tuid, mont)
        clear_st(uid)
        bot.send_message(uid,
            f"<b>+{mont:.0f} XAF</b> credite a {tuid}\n"
            f"Raison : {raison}\nSolde : <b>{new_s:.0f} XAF</b>")
        try:
            bot.send_message(tuid,
                f"<b>Credit recu !</b>\n\n"
                f"<b>+{mont:.0f} XAF</b> ajoutes par l'administrateur.\n"
                f"Raison : {raison}\n"
                f"Nouveau solde : <b>{new_s:.0f} XAF</b>")
        except: pass
        send_admin_panel(uid)
        return

    if st == "ADMIN_SEARCH" and is_admin(uid):
        if text == "/annuler":
            clear_st(uid); send_admin_panel(uid); return
        clear_st(uid)
        bot.send_message(uid, get_user_info_text(text))
        send_admin_panel(uid)
        return

    if st == "ADMIN_BAN" and is_admin(uid):
        if text == "/annuler":
            clear_st(uid); send_admin_panel(uid); return
        try: tuid=int(text)
        except: bot.send_message(uid,"ID invalide."); return
        with pg() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE users SET is_banned=TRUE WHERE user_id=%s",(tuid,))
        invalidate_user_cache(tuid)
        clear_st(uid)
        bot.send_message(uid,f"Utilisateur <code>{tuid}</code> banni.")
        try: bot.send_message(tuid,"Votre compte a ete suspendu. Contactez le support.")
        except: pass
        send_admin_panel(uid)
        return

    if st == "ADMIN_UNBAN" and is_admin(uid):
        if text == "/annuler":
            clear_st(uid); send_admin_panel(uid); return
        try: tuid=int(text)
        except: bot.send_message(uid,"ID invalide."); return
        with pg() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE users SET is_banned=FALSE WHERE user_id=%s",(tuid,))
        invalidate_user_cache(tuid)
        clear_st(uid)
        bot.send_message(uid,f"Utilisateur <code>{tuid}</code> debanni.")
        try: bot.send_message(tuid,"Votre compte a ete reactive. Bon jeu !")
        except: pass
        send_admin_panel(uid)
        return

    if st == "ADMIN_BROADCAST" and is_admin(uid):
        if text == "/annuler":
            clear_st(uid); send_admin_panel(uid); return
        clear_st(uid)
        with pg() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT user_id FROM users WHERE is_banned=FALSE")
                all_ids = [r[0] for r in cur.fetchall()]
        sent = 0; failed = 0
        bot.send_message(uid,f"Envoi en cours a {len(all_ids)} joueurs...")
        for tid in all_ids:
            try:
                bot.send_message(tid, f"<b>Message de NKAP EXPRESS</b>\n\n{text}")
                sent += 1
                time.sleep(0.05)
            except: failed += 1
        bot.send_message(uid,
            f"<b>Broadcast termine</b>\n\n"
            f"Envoyes : <b>{sent}</b>\n"
            f"Echoues : <b>{failed}</b>")
        send_admin_panel(uid)
        return

    if st == "ASK_NAME":
        if len(text)<2 or len(text)>20:
            bot.send_message(uid,"Nom entre 2 et 20 caracteres :"); return
        d = get_st(uid).get("data",{})
        parrain_id = d.get("parrain_id",0) or None

        # Auto-generate unique 5-digit PIN
        pin = generate_pin()

        ok = create_user(
            uid=uid,
            username=msg.from_user.username or "",
            tg_name=msg.from_user.first_name or "",
            custom_name=text,
            pin=pin,
            parrain_id=parrain_id,
        )
        clear_st(uid)
        if not ok:
            bot.send_message(uid,"Erreur lors de la creation. Tapez /start."); return

        if parrain_id:
            enregistrer_filleul(parrain_id, uid)
            try:
                u_new = get_user(uid)
                bot.send_message(parrain_id,
                    f"<b>Nouveau filleul !</b>\n\n"
                    f"<b>{u_new['custom_name']}</b> a rejoint NKAP EXPRESS via votre lien.\n"
                    f"Bonus : <b>+{BONUS_PARRAIN} XAF</b> credites !\n"
                    f"Solde : <b>{get_solde(parrain_id):.0f} XAF</b>",
                    parse_mode="HTML")
            except: pass

        bonus = BONUS_BIENVENUE + (BONUS_FILLEUL if parrain_id else 0)
        bot.send_message(uid,
            f"<b>Compte cree avec succes !</b>\n\n"
            f"Nom : <b>{text}</b>\n"
            f"Bonus de bienvenue : <b>+{bonus} XAF</b>\n\n"
            f"<b>Votre code secret (PIN) : <code>{pin}</code></b>\n"
            f"Notez-le bien -- il vous sera demande pour jouer et retirer.\n"
            f"Ne le partagez jamais !\n\n"
            f"Appuyez sur <b>Jouer</b> pour commencer !",
            parse_mode="HTML",
            reply_markup=main_kb(uid))
        return

    if st == "DEPOT_MONTANT":
        try: m=float(text)
        except: bot.send_message(uid,"Entrez un nombre :"); return
        from config import MIN_DEPOT
        if m<MIN_DEPOT: bot.send_message(uid,f"Minimum {MIN_DEPOT} XAF :"); return
        set_st(uid,"DEPOT_TEL",montant=m)
        bot.send_message(uid,"Entrez votre numero Mobile Money :"); return

    if st == "DEPOT_TEL":
        m = get_st(uid).get("data",{}).get("montant",0)
        clear_st(uid)
        with pg() as conn:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO depot_demandes(user_id,montant,telephone) VALUES(%s,%s,%s)",
                            (uid,m,text))
        bot.send_message(uid,
            f"<b>Demande enregistree</b>\n\n"
            f"Montant : <b>{m:.0f} XAF</b> | Numero : <b>{text}</b>\n"
            f"Envoyez le montant au numero admin.\n"
            f"Credit sous 5-15 min.",
            reply_markup=main_kb(uid)); return

    if text == "ADMIN PANEL" and is_admin(uid):
        send_admin_panel(uid); return

    u = get_user(uid)
    if not u:
        bot.send_message(uid,"Tapez /start pour creer votre compte."); return
    update_last_seen(uid)

    if text == "Mon Solde":
        bot.send_message(uid,
            f"<b>Votre Solde</b>\n\n"
            f"Disponible : <b>{get_solde(uid):.0f} XAF</b>\n"
            f"Mises jouees : <b>{u.get('total_mises',0)}</b>\n"
            f"Gains totaux : <b>{float(u.get('total_gains',0)):.0f} XAF</b>")

    elif text == "Historique":
        bot.send_message(uid,
            f"<b>20 Derniers Tirages</b>\n\n"
            + fmt_history_bot(get_history_full(20)) +
            "\n\n<i>Meme historique que sur le site.</i>")

    elif text == "Deposer":
        set_st(uid,"DEPOT_MONTANT")
        bot.send_message(uid,"Montant a deposer (minimum 100 XAF) :")

    elif text == "Retirer":
        bot.send_message(uid,
            f"Solde : <b>{get_solde(uid):.0f} XAF</b>\n\n"
            f"Commande : <code>/retirer MONTANT NUMERO</code>\n"
            f"Exemple : <code>/retirer 500 699123456</code>")

    elif text == "Parrainage":
        stats = get_stats_parrain(uid)
        bot.send_message(uid,
            f"<b>Votre Parrainage</b>\n\n"
            f"Filleuls actifs : <b>{stats['filleuls']}</b>\n"
            f"Bonus total : <b>{stats['bonus_total']} XAF</b>\n"
            f"Bonus par filleul : <b>{BONUS_PARRAIN} XAF</b>\n\n"
            f"Votre lien :\n<code>{stats['lien']}</code>\n\n"
            f"Chaque ami inscrit = <b>+{BONUS_PARRAIN} XAF</b> pour vous !")

    elif text == "Classement":
        lb = get_leaderboard(10)
        if not lb:
            bot.send_message(uid,"Aucun gagnant aujourd'hui. Soyez le premier !"); return
        medals = ["1.","2.","3.","4.","5.","6.","7.","8.","9.","10."]
        lines  = [f"{medals[i]} <b>{p.get('custom_name') or p.get('tg_name','?')}</b> -- {float(p['gains_jour']):.0f} XAF"
                  for i,p in enumerate(lb)]
        bot.send_message(uid,"<b>Top 10 du Jour</b>\n\n"+"\n".join(lines))

    elif text == "Mon Profil":
        stats = get_stats_parrain(uid)
        bot.send_message(uid,
            f"<b>Votre Profil</b>\n\n"
            f"{u.get('custom_name') or u.get('tg_name')}\n"
            f"@{u.get('username') or '--'}\n"
            f"Solde : <b>{get_solde(uid):.0f} XAF</b>\n"
            f"Mises : <b>{u.get('total_mises',0)}</b>\n"
            f"Gains : <b>{float(u.get('total_gains',0)):.0f} XAF</b>\n"
            f"Meilleur gain : <b>{float(u.get('meilleur_gain',0)):.0f} XAF</b>\n"
            f"Filleuls : <b>{stats['filleuls']}</b>\n"
            f"Membre depuis : <b>{str(u.get('created_at',''))[:10]}</b>")

    elif text == "Aide":
        bot.send_message(uid,
            "<b>Guide NKAP EXPRESS</b>\n\n"
            "1. /start - Creez votre compte (nom + PIN 5 chiffres)\n"
            "2. Recevez <b>200 XAF</b> offerts\n"
            "3. Ouvrez Jouer - Entrez votre PIN - Jouez\n"
            "4. Misez 1-500 XAF sur un numero 0-5\n"
            "5. Numero gagnant = <b>5x votre mise !</b>\n\n"
            "Depot : bouton Deposer\n"
            "Retrait : <code>/retirer MONTANT NUMERO</code>\n"
            "Parrainage : bouton Parrainage (+250 XAF/filleul)\n\n"
            "Votre PIN est <b>strictement personnel</b>.")

    else:
        bot.send_message(uid,"Utilisez le menu.", reply_markup=main_kb(uid))


def rl(uid, action, n=30):
    if not check_rate_limit(uid, action, n):
        return jsonify({"success":False,"message":"Trop de requetes."}), 429
    return None

@app.after_request
def add_cors(response):
    response.headers["Access-Control-Allow-Origin"]  = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, ngrok-skip-browser-warning"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return response

@app.route("/", methods=["GET"])
def serve_index():
    import os
    from flask import send_file as _sf
    idx = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "index.html"))
    return _sf(idx)

@app.route("/api/server_status", methods=["GET","OPTIONS"])
def api_server_status():
    if freq.method == "OPTIONS": return jsonify({}), 200
    from database import r as rc
    srv       = get_server_state()
    countdown = int(rc().get("server_countdown") or 0)
    return jsonify({
        "open":      srv.get("is_open", False),
        "open_key":  srv.get("open_key","") if srv.get("is_open") else "",
        "countdown": countdown,
    })

@app.route("/api/auth", methods=["POST","OPTIONS"])
def api_auth():
    if freq.method == "OPTIONS": return jsonify({}), 200
    d   = freq.json or {}
    uid = d.get("user_id")
    pin = str(d.get("pin",""))
    if not uid or len(pin)!=5 or not pin.isdigit():
        return jsonify({"success":False,"message":"Donnees invalides."})
    lim = rl(uid,"auth",5)
    if lim: return lim
    u = get_user(int(uid))
    if not u:
        return jsonify({"success":False,"message":"Compte introuvable. Inscrivez-vous via Telegram."})
    if u.get("is_banned"):
        return jsonify({"success":False,"message":"Compte suspendu."})
    pin_result = verify_pin(int(uid), pin)
    if not pin_result["ok"]:
        return jsonify({"success":False,"message":pin_result["message"],
                        "lockout_secs":pin_result["lockout_secs"]})
    update_last_seen(int(uid))
    hist = get_history_full(20)
    return jsonify({
        "success":      True,
        "solde":        get_solde(int(uid)),
        "custom_name":  u["custom_name"],
        "tg_name":      u["tg_name"],
        "history_full": hist,
        "history_nums": [h["numero"] for h in hist],
    })

@app.route("/api/mise", methods=["POST","OPTIONS"])
def api_mise():
    if freq.method == "OPTIONS": return jsonify({}), 200
    d      = freq.json or {}
    uid    = d.get("user_id")
    pin    = str(d.get("pin",""))
    mise   = d.get("mise")
    numero = d.get("numero")
    if not all([uid, pin, mise is not None, numero is not None]):
        return jsonify({"success":False,"message":"Donnees incompletes."})
    lim = rl(uid,"mise",10)
    if lim: return lim
    _pr = verify_pin(int(uid), pin)
    if not _pr["ok"]:
        return jsonify({"success":False,"message":_pr["message"],"lockout_secs":_pr["lockout_secs"]})
    try: mise=float(mise); numero=int(numero)
    except: return jsonify({"success":False,"message":"Valeurs invalides."})
    return jsonify(engine.place_bet(int(uid), mise, numero))

@app.route("/api/depot", methods=["POST","OPTIONS"])
def api_depot():
    if freq.method == "OPTIONS": return jsonify({}), 200
    d         = freq.json or {}
    uid       = d.get("user_id")
    pin       = str(d.get("pin",""))
    montant   = d.get("montant")
    telephone = d.get("telephone","").strip()
    if not all([uid, pin, montant]):
        return jsonify({"success":False,"message":"Donnees manquantes."})
    lim = rl(uid,"depot",3)
    if lim: return lim
    _pr = verify_pin(int(uid), pin)
    if not _pr["ok"]:
        return jsonify({"success":False,"message":_pr["message"],"lockout_secs":_pr["lockout_secs"]})
    try: montant=float(montant)
    except: return jsonify({"success":False,"message":"Montant invalide."})
    from config import MIN_DEPOT
    if montant<MIN_DEPOT:
        return jsonify({"success":False,"message":f"Minimum {MIN_DEPOT} XAF."})
    with pg() as conn:
        with conn.cursor() as c:
            c.execute("INSERT INTO depot_demandes(user_id,montant,telephone) VALUES(%s,%s,%s)",
                      (uid,montant,telephone))
    try:
        bot.send_message(int(uid),
            f"<b>Demande de depot recue</b>\n\n"
            f"Montant : <b>{montant:.0f} XAF</b>\n"
            f"Envoyez ce montant au numero admin et attendez la confirmation.")
    except: pass
    return jsonify({"success":True,"message":f"Demande de {montant:.0f} XAF envoyee."})

@app.route("/api/retrait", methods=["POST","OPTIONS"])
def api_retrait():
    if freq.method == "OPTIONS": return jsonify({}), 200
    d         = freq.json or {}
    uid       = d.get("user_id")
    pin       = str(d.get("pin",""))
    montant   = d.get("montant")
    telephone = d.get("telephone","").strip()
    if not all([uid, pin, montant, telephone]):
        return jsonify({"success":False,"message":"Donnees manquantes."})
    _pr = verify_pin(int(uid), pin)
    if not _pr["ok"]:
        return jsonify({"success":False,"message":_pr["message"],"lockout_secs":_pr["lockout_secs"]})
    try: montant=float(montant)
    except: return jsonify({"success":False,"message":"Montant invalide."})
    from config import MIN_RETRAIT
    if montant<MIN_RETRAIT:
        return jsonify({"success":False,"message":f"Minimum {MIN_RETRAIT} XAF."})
    solde=get_solde(int(uid))
    if solde<montant:
        return jsonify({"success":False,"message":f"Solde insuffisant ({solde:.0f} XAF)."})
    new_s=update_solde(int(uid),-montant)
    with pg() as conn:
        with conn.cursor() as c:
            c.execute("INSERT INTO retrait_demandes(user_id,montant,telephone) VALUES(%s,%s,%s)",
                      (uid,montant,telephone))
    try:
        bot.send_message(int(uid),
            f"<b>Retrait en cours</b>\nMontant : <b>{montant:.0f} XAF</b>\n"
            f"Numero : <b>{telephone}</b>\nSolde restant : <b>{new_s:.0f} XAF</b>")
    except: pass
    return jsonify({"success":True,"message":f"Retrait de {montant:.0f} XAF en cours.","new_solde":new_s})

@app.route("/api/state", methods=["GET","OPTIONS"])
def api_state():
    if freq.method == "OPTIONS": return jsonify({}), 200
    uid   = freq.args.get("user_id")
    state = engine.get_state(int(uid) if uid else None)
    return jsonify(state)

@app.route("/api/parrainage", methods=["GET","OPTIONS"])
def api_parrainage():
    if freq.method == "OPTIONS": return jsonify({}), 200
    uid = freq.args.get("user_id")
    if not uid: return jsonify({"success":False})
    return jsonify({"success":True, **get_stats_parrain(int(uid))})

@app.route("/api/leaderboard", methods=["GET","OPTIONS"])
def api_leaderboard():
    if freq.method == "OPTIONS": return jsonify({}), 200
    return jsonify(get_leaderboard(10))

@app.route("/webhook", methods=["POST"])
def webhook():
    if freq.headers.get("X-Telegram-Bot-Api-Secret-Token") != WEBHOOK_SECRET:
        abort(403)
    update = telebot.types.Update.de_json(freq.data.decode("utf-8"))
    bot.process_new_updates([update])
    return "OK", 200


if __name__ == "__main__":
    log.info("=" * 60)
    log.info("   NKAP EXPRESS BOT v4 -- FULL POWER + ADMIN PANEL")
    log.info("=" * 60)

    # 1. Database + history seed
    init_db()
    fill_history_if_empty()
    set_server_open(False, "")

    # 2. Game engine
    engine.start()

    # 3. Remove any existing webhook to ensure clean polling mode
    try:
        bot.remove_webhook()
        log.info("Webhook removed — using polling mode.")
    except Exception as e:
        log.warning(f"remove_webhook warning (non-fatal): {e}")

    # 4. Register bot commands
    try:
        bot.set_my_commands([
            telebot.types.BotCommand("start",    "Creer un compte / Menu"),
            telebot.types.BotCommand("retirer",  "Retirer des fonds"),
            telebot.types.BotCommand("ouvrir",   "Admin: ouvrir le site"),
            telebot.types.BotCommand("fermer",   "Admin: fermer le site"),
            telebot.types.BotCommand("valider",  "Admin: valider depot"),
            telebot.types.BotCommand("stats",    "Admin: statistiques"),
            telebot.types.BotCommand("ban",      "Admin: bannir joueur"),
            telebot.types.BotCommand("annuler",  "Annuler l'action en cours"),
        ])
    except Exception as e:
        log.warning(f"set_my_commands warning (non-fatal): {e}")

    # 5. Flask API in background thread — port 8080 for Replit
    def run_flask():
        app.run(host='0.0.0.0', port=8080, debug=False, use_reloader=False)

    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    log.info("Flask API server started on port 8080")
    log.info("API endpoints: /api/auth /api/mise /api/state /api/depot /api/retrait /api/leaderboard /api/parrainage")

    # 6. Small delay so any previous session fully disconnects (avoids 409)
    log.info("Waiting 3s before polling to avoid Telegram 409 conflict...")
    time.sleep(3)

    # 7. Start polling — stable mode for Replit
    log.info("Bot online (Polling mode)... Ready!")
    bot.infinity_polling(skip_pending=True, timeout=30, long_polling_timeout=20)
