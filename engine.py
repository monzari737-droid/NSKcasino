"""
engine.py — NKAP EXPRESS
Moteur de jeu : Honey Pot, Predato (Bouclier de Caisse), Armée de l'Ombre (Bots)
"""
import random
import time
import threading
import logging
from datetime import datetime

from database import (
    get_solde, update_solde, get_user,
    insert_bet, resolve_bet, add_history,
    get_history_full, get_history_nums,
    get_server_state, get_admin_stats,
    get_caisse, update_caisse,
    get_nb_users, blur, tour_id as new_tour_id
)
from config import (
    PHASE_BETS, PHASE_RESULT, WIN_MULTIPLIER,
    MAX_MISE, MIN_MISE,
    HONEYPOT_THRESHOLD, HONEYPOT_CYCLE,
    PREDATO_THRESHOLD, PREDATO_MIN_USERS,
)

log = logging.getLogger(__name__)

# ── Armée de l'Ombre ──────────────────────────────────────
WORLD = {
    "SN": ["Moussa","Fatoumata","Traore","Diallo","Aminata","Seydou","Ibrahima"],
    "CM": ["Dieudonne","Ines","Kamga","Aristide","Celeste","Rodrigue","Bertrand"],
    "GH": ["Kofi","Ama","Thabo","Kwame","Abena","Yaw","Akosua"],
    "CI": ["Yao","Adjoua","Kouassi","Bamba","Mariam","Karim","Affoue"],
    "NG": ["Emeka","Ngozi","Chukwu","Adaeze","Tunde","Kemi","Biodun"],
    "FR": ["Lucas","Emma","Martin","Durand","Lea","Hugo","Clemence"],
    "ES": ["Mateo","Sofia","Garcia","Carlos","Isabella","Miguel","Valentina"],
    "DE": ["Lukas","Hanna","Mueller","Schmidt","Klaus","Petra","Max"],
    "IT": ["Marco","Giulia","Rossi","Ferrari","Lorenzo","Chiara","Luca"],
    "US": ["Jackson","Olivia","Smith","Johnson","Tyler","Madison","Brooklyn"],
    "BR": ["Rafael","Ana","Silva","Costa","Pedro","Camila","Fernanda"],
    "JP": ["Kenji","Yuki","Tanaka","Sato","Hiro","Aiko","Haruki"],
    "RU": ["Ivan","Natasha","Petrov","Sokolov","Olga","Dmitri","Anastasia"],
    "MA": ["Youssef","Fatima","Benali","Idrissi","Rachid","Nadia","Amine"],
}
FLAGS = list(WORLD.keys())


def gen_bot(mise_override=None):
    flag = random.choice(FLAGS)
    name = random.choice(WORLD[flag])
    rv   = random.random()
    mise = mise_override or (
        random.randint(1, 50)    if rv < .50 else
        random.randint(51, 200)  if rv < .82 else
        random.randint(201, 500)
    )
    return {
        "flag":   flag,
        "name":   blur(name),
        "mise":   mise,
        "num":    random.randint(0, 5),
        "is_bot": True
    }


class GameEngine:
    def __init__(self):
        self.lock          = threading.Lock()
        self.tour_id       = None
        self.phase         = "idle"
        self.bots          = []
        self.real_bets     = []
        self.win_number    = None
        self._notify_cb    = None
        self._round_count  = 0
        self._hp_idx       = 0      # honey pot index cycle 0→5

    def set_notify_callback(self, cb):
        self._notify_cb = cb

    def start(self):
        t = threading.Thread(target=self._loop, daemon=True, name="GameEngine")
        t.start()
        log.info("✅ Game engine started.")

    def _loop(self):
        while True:
            try:
                self._phase_betting()
                self._phase_result()
            except Exception as e:
                log.error(f"Engine error: {e}", exc_info=True)
                time.sleep(5)

    def _phase_betting(self):
        # 0 à 30 bots aléatoires par tour
        n_bots = random.randint(0, 30)
        with self.lock:
            self.tour_id    = new_tour_id()
            self.phase      = "betting"
            self.real_bets  = []
            self.win_number = None
            self.bots       = [gen_bot() for _ in range(n_bots)]

        log.info(f"BETS OPEN — {self.tour_id} — {n_bots} bots")
        time.sleep(PHASE_BETS)

    # ── PREDATO — Bouclier de Caisse ─────────────────────────
    def _predato_win(self, real_bets: list) -> int | None:
        """
        Si nb_users >= PREDATO_MIN_USERS et que les mises réelles
        sur un même numéro dépassent PREDATO_THRESHOLD * caisse →
        Predato force le numéro le MOINS misé.
        Retourne le numéro forcé ou None.
        """
        try:
            nb_users = get_nb_users()
            if nb_users < PREDATO_MIN_USERS:
                return None

            caisse = get_caisse()
            seuil  = caisse * PREDATO_THRESHOLD

            # Cumul des mises réelles par numéro
            cumul = [0.0] * 6
            for b in real_bets:
                cumul[b["numero"]] += b["mise"]

            # Gain potentiel maximum = mise_max_num * WIN_MULTIPLIER
            mise_max = max(cumul)
            gain_potentiel = mise_max * WIN_MULTIPLIER

            if gain_potentiel > seuil:
                # Force le numéro le moins misé (protège la caisse)
                forced = int(cumul.index(min(cumul)))
                log.warning(
                    f"[PREDATO] Gain potentiel {gain_potentiel:.0f} > seuil {seuil:.0f} "
                    f"→ Force N°{forced} (caisse={caisse:.0f})"
                )
                return forced
        except Exception as e:
            log.error(f"Predato error: {e}")
        return None

    def _phase_result(self):
        with self.lock:
            self._round_count += 1
            round_n = self._round_count
            bets    = list(self.real_bets)
            bots    = list(self.bots)
            tid     = self.tour_id

        # ── 1. Honey Pot (50 premiers joueurs, tous les 6 tours) ──
        try:
            nb_users = get_nb_users()
        except Exception:
            nb_users = 999

        honeypot_active = (
            nb_users < HONEYPOT_THRESHOLD
            and round_n % HONEYPOT_CYCLE == 0
        )

        # ── 2. Predato (après 50 joueurs) ────────────────────────
        predato_num = self._predato_win(bets) if not honeypot_active else None

        # ── 3. Calcul du numéro gagnant ──────────────────────────
        with self.lock:
            if honeypot_active:
                win = self._hp_idx % 6
                self._hp_idx += 1
                log.info(
                    f"[HONEYPOT] Round {round_n} | players={nb_users} "
                    f"| forced WIN={win}"
                )
            elif predato_num is not None:
                win = predato_num
            else:
                win = random.SystemRandom().randint(0, 5)

            self.phase      = "result"
            self.win_number = win

        log.info(f"WIN N°{win} | Tour {tid} | Round {round_n}")

        # ── 4. Résolution des mises réelles ──────────────────────
        total_mise_bots = sum(b["mise"] for b in bots)
        total_mise_real = sum(b["mise"] for b in bets)
        total_mise      = total_mise_bots + total_mise_real
        total_players   = len(bots) + len(bets)

        best_winner_uid   = None
        best_winner_name  = None
        best_winner_solde = None

        for bet in bets:
            uid   = bet["user_id"]
            mise  = bet["mise"]
            bid   = bet["bet_id"]
            won   = bet["numero"] == win
            gain  = mise * WIN_MULTIPLIER if won else 0.0

            resolve_bet(bid, won, gain, uid)

            if won:
                new_s = update_solde(uid, gain)
                # Caisse : encaisse les mises perdantes, paie les gains
                update_caisse(mise - gain)
                u   = get_user(uid)
                nom = blur(u["custom_name"] or u["tg_name"]) if u else "Joueur"
                if best_winner_uid is None or gain > (best_winner_solde or 0):
                    best_winner_uid   = uid
                    best_winner_name  = nom
                    best_winner_solde = new_s
                if self._notify_cb:
                    try:
                        self._notify_cb(
                            uid,
                            f"<b>🎉 VOUS AVEZ GAGNÉ !</b>\n\n"
                            f"Numéro gagnant : <b>{win}</b>\n"
                            f"Gain : <b>+{gain:.0f} XAF</b>\n"
                            f"Nouveau solde : <b>{new_s:.0f} XAF</b>"
                        )
                    except Exception as e:
                        log.warning(f"Notif winner {uid}: {e}")
            else:
                # Caisse encaisse les mises perdantes
                update_caisse(mise)

        # ── 5. Gagnant fictif si aucun humain n'a gagné ──────────
        if not best_winner_name:
            bot_winners = [b for b in bots if b["num"] == win]
            if bot_winners:
                w = random.choice(bot_winners)
                best_winner_name  = f"{w['flag']}-{w['name']}"
                best_winner_solde = round(random.uniform(300, 8000), 0)
            else:
                flag_f = random.choice(FLAGS)
                nom_f  = random.choice(WORLD[flag_f])
                best_winner_name  = f"{flag_f}-{blur(nom_f)}"
                best_winner_solde = round(random.uniform(300, 8000), 0)

        # ── 6. Sauvegarde en base ─────────────────────────────────
        add_history(
            numero=win, tid=tid,
            winner_uid=best_winner_uid,
            winner_name=best_winner_name,
            winner_solde=best_winner_solde,
            total_players=total_players,
            total_mise=total_mise
        )

        time.sleep(PHASE_RESULT)
        with self.lock:
            self.phase = "idle"

    # ── API publique : placer une mise ───────────────────────
    def place_bet(self, uid: int, mise: float, numero: int) -> dict:
        with self.lock:
            if self.phase != "betting":
                return {"success": False, "message": "Mises fermées. Attendez le prochain tour."}
            if not (MIN_MISE <= mise <= MAX_MISE):
                return {"success": False, "message": f"Mise invalide ({MIN_MISE}–{MAX_MISE} XAF)."}
            if not (0 <= numero <= 5):
                return {"success": False, "message": "Numéro invalide (0–5)."}

            solde = get_solde(uid)
            if solde < mise:
                return {"success": False, "message": f"Solde insuffisant ({solde:.0f} XAF)."}

            new_s = update_solde(uid, -mise)
            bid   = insert_bet(uid, self.tour_id, numero, mise)
            self.real_bets.append({
                "bet_id":  bid,
                "user_id": uid,
                "mise":    mise,
                "numero":  numero
            })

        log.info(f"Bet: user={uid} mise={mise} num={numero}")
        return {
            "success":   True,
            "message":   f"Mise de {mise:.0f} XAF sur N°{numero} enregistrée !",
            "new_solde": new_s
        }

    # ── État complet pour le frontend ────────────────────────
    def get_state(self, uid: int = None) -> dict:
        with self.lock:
            state = {
                "phase":         self.phase,
                "tour_id":       self.tour_id,
                "win_number":    self.win_number if self.phase == "result" else None,
                "bots":          list(self.bots),
                "real_count":    len(self.real_bets),
                "total_players": len(self.bots) + len(self.real_bets),
                "history_full":  get_history_full(20),
                "history_nums":  get_history_nums(20),
            }
        if uid:
            state["solde"] = get_solde(uid)
        srv = get_server_state()
        state["server_open"] = srv.get("is_open", False)
        state["open_key"]    = srv.get("open_key", "") if srv.get("is_open") else ""
        return state


engine = GameEngine()
