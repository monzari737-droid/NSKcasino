import random, time, threading, logging
from datetime import datetime
from database import (
    get_solde, update_solde, get_user,
    insert_bet, resolve_bet, add_history,
    get_history_full, get_history_nums,
    get_server_state, get_admin_stats, blur, tour_id as new_tour_id
)
from config import (
    PHASE_BETS, PHASE_RESULT, WIN_MULTIPLIER,
    MAX_MISE, MIN_MISE
)

log = logging.getLogger(__name__)

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
    rv = random.random()
    mise = mise_override or (
        random.randint(1,50) if rv<.5
        else random.randint(51,200) if rv<.82
        else random.randint(201,500)
    )
    return {"flag":flag,"name":blur(name),"mise":mise,"num":random.randint(0,5),"is_bot":True}


HONEYPOT_THRESHOLD = 50   # activate honey pot when real players < this
HONEYPOT_CYCLE     = 6    # every 6th round is forced

class GameEngine:
    def __init__(self):
        self.lock         = threading.Lock()
        self.tour_id      = None
        self.phase        = "idle"
        self.bots         = []
        self.real_bets    = []
        self.win_number   = None
        self._notify_cb   = None
        self._round_count  = 0   # total rounds played since start
        self._hp_idx       = 0   # honeypot number cycle index (0-5)

    def set_notify_callback(self, cb):
        self._notify_cb = cb

    def start(self):
        t = threading.Thread(target=self._loop, daemon=True, name="GameEngine")
        t.start()
        log.info("Game engine started.")

    def _loop(self):
        while True:
            try:
                self._phase_betting()
                self._phase_result()
            except Exception as e:
                log.error(f"Engine error: {e}", exc_info=True)
                time.sleep(5)

    def _phase_betting(self):
        n_bots = random.randint(8, 30)
        with self.lock:
            self.tour_id    = new_tour_id()
            self.phase      = "betting"
            self.real_bets  = []
            self.win_number = None
            self.bots       = [gen_bot() for _ in range(n_bots)]

        log.info(f"BETS OPEN -- {self.tour_id} -- {n_bots} bots")
        time.sleep(PHASE_BETS)

    def _phase_result(self):
        # ── Honey Pot Logic ──────────────────────────────────────────────────
        # While real player base < 50: every 6th round uses a predictable
        # number cycling 0→1→2→3→4→5→0→... to create a statistical pattern
        # detectable by the web casino for marketing / engagement purposes.
        try:
            stats = get_admin_stats()
            nb_players = int(stats.get("nb_users", 0))
        except Exception:
            nb_players = 999  # fail-safe: disable honey pot on DB error

        with self.lock:
            self._round_count += 1
            round_n = self._round_count

            honeypot_active = (
                nb_players < HONEYPOT_THRESHOLD
                and round_n % HONEYPOT_CYCLE == 0
            )

            if honeypot_active:
                forced_num = self._hp_idx % 6
                self._hp_idx += 1
                win = forced_num
                log.info(
                    f"[HONEYPOT] Round {round_n} | players={nb_players} "
                    f"| forced WIN={win} (hp_idx={self._hp_idx})"
                )
            else:
                win = random.SystemRandom().randint(0, 5)

            self.phase      = "result"
            self.win_number = win
            bets  = list(self.real_bets)
            bots  = list(self.bots)
            tid   = self.tour_id

        log.info(f"WINNING NUMBER: {win} | Tour {tid} | Round {round_n}")

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
            gain  = mise * WIN_MULTIPLIER if won else 0

            resolve_bet(bid, won, gain, uid)

            if won:
                new_s = update_solde(uid, gain)
                u     = get_user(uid)
                nom   = blur(u["custom_name"] or u["tg_name"]) if u else "Joueur"
                if best_winner_uid is None or gain > (best_winner_solde or 0):
                    best_winner_uid   = uid
                    best_winner_name  = nom
                    best_winner_solde = new_s
                if self._notify_cb:
                    try:
                        self._notify_cb(uid,
                            f"<b>VOUS AVEZ GAGNE !</b>\n\n"
                            f"Numero gagnant : <b>{win}</b>\n"
                            f"Gain : <b>+{gain:.0f} XAF</b>\n"
                            f"Nouveau solde : <b>{new_s:.0f} XAF</b>")
                    except Exception as e:
                        log.warning(f"Notif winner {uid}: {e}")

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

    def place_bet(self, uid: int, mise: float, numero: int) -> dict:
        with self.lock:
            if self.phase != "betting":
                return {"success": False, "message": "Mises fermees. Attendez le prochain tour."}
            if not (MIN_MISE <= mise <= MAX_MISE):
                return {"success": False, "message": f"Mise invalide ({MIN_MISE}-{MAX_MISE} XAF)."}
            if not (0 <= numero <= 5):
                return {"success": False, "message": "Numero invalide (0-5)."}

            solde = get_solde(uid)
            if solde < mise:
                return {"success": False, "message": f"Solde insuffisant ({solde:.0f} XAF)."}

            new_s = update_solde(uid, -mise)
            bid   = insert_bet(uid, self.tour_id, numero, mise)
            self.real_bets.append({"bet_id":bid,"user_id":uid,"mise":mise,"numero":numero})

        log.info(f"Bet: user={uid} mise={mise} num={numero}")
        return {"success":True,"message":f"Mise de {mise:.0f} XAF sur N{numero} !",
                "new_solde": new_s}

    def get_state(self, uid: int = None) -> dict:
        with self.lock:
            state = {
                "phase":         self.phase,
                "tour_id":       self.tour_id,
                "win_number":    self.win_number if self.phase=="result" else None,
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
