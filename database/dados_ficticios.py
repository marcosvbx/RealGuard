"""
Dados fictícios no banco REAL do RealGuard.

Para o dashboard já abrir com vida (gráficos, filtros, relatórios), a primeira
subida com o banco sem nenhum aluno cria ~90 dias de vida escolar coerente:

* 120 alunos fictícios em 6 turmas (3 turnos), com perfis de comportamento
  (pontual, atrasado crônico, faltoso, irregular, sai cedo, sai e volta),
  novatos no meio do período e alguns sem termo de consentimento;
* calendário com um sábado letivo e um conselho de classe só à noite;
* passagens de câmera no passado, um "dia de chuva" com muitos atrasos e um
  dia com a câmera da manhã fora do ar (presenças registradas à mão);
* alertas de rosto obstruído com todos os desfechos (2 pendentes hoje);
* 2 câmeras extras cadastradas (inativas), para o gerenciador de monitores.

Regras de segurança:

* NENHUM usuário é criado — o primeiro acesso continua pelo terminal;
* NENHUM responsável tem Telegram vinculado — nenhuma mensagem sai;
* NENHUM vetor de rosto é criado — não atrapalha o reconhecimento de
  pessoas reais (cadastre rostos de verdade para testar);
* todo registro criado aqui leva ficticio = 1 e pode ser removido pela tela
  (Administração → Dados fictícios) sem tocar no que for real.

A presença do dia é calculada pelo MESMO código do sistema (database/attendance.py).
Mesma semente => mesmos dados.
"""

import json
import logging
import math
import os
import random
from datetime import time as dtime, timedelta
from config.clock import date, datetime
from typing import Any, Dict, List, Optional, Tuple

from database.connection import get_db_connection

logger = logging.getLogger(__name__)

FMT = "%Y-%m-%d %H:%M:%S"
STATE_KEY = "dados_ficticios"
ACTOR = {"id": None, "username": "ficticio", "full_name": "Registro fictício"}

FIRST = ["Ana", "Beatriz", "Bruno", "Caio", "Camila", "Carlos", "Clara", "Daniel", "Davi", "Eduarda", "Enzo",
         "Felipe", "Gabriel", "Giovana", "Heitor", "Helena", "Igor", "Isabela", "João", "Júlia", "Laura", "Leonardo",
         "Lívia", "Lorena", "Lucas", "Luiza", "Manuela", "Marcos", "Maria", "Mateus", "Miguel", "Nicolas", "Otávio",
         "Pedro", "Rafael", "Rafaela", "Samuel", "Sofia", "Theo", "Valentina", "Vinícius", "Yasmin"]
LAST = ["Almeida", "Andrade", "Araújo", "Barbosa", "Barros", "Cardoso", "Carvalho", "Castro", "Costa", "Dias",
        "Ferreira", "Freitas", "Gomes", "Lima", "Lopes", "Martins", "Melo", "Mendes", "Moreira", "Nascimento",
        "Oliveira", "Pereira", "Pinto", "Ribeiro", "Rocha", "Santos", "Silva", "Soares", "Souza", "Teixeira"]
GUARDIAN_FIRST = ["Adriana", "Alexandre", "Cláudia", "Cristiane", "Eduardo", "Fábio", "Fernanda", "Juliana",
                  "Marcelo", "Patrícia", "Renata", "Ricardo", "Roberta", "Rodrigo", "Simone", "Vanessa"]
WOMEN = {"Adriana", "Cláudia", "Cristiane", "Fernanda", "Juliana", "Patrícia", "Renata", "Roberta", "Simone",
         "Vanessa"}
SECOND = [("Avó", ["Maria", "Lúcia", "Aparecida", "Tereza"]), ("Avô", ["José", "Antônio", "Sebastião"]),
          ("Tia", ["Carla", "Sandra", "Luciana"]), ("Tio", ["Paulo", "André", "Márcio"])]

# perfil: (prob. de faltar, média do atraso em min, desvio, prob. saída antecipada, prob. sair e voltar)
PERSONAS = {
    "pontual": (0.03, -9, 5, 0.02, 0.00),
    "atrasado": (0.05, 12, 9, 0.03, 0.00),
    "faltoso": (0.24, -5, 7, 0.04, 0.00),
    "irregular": (0.11, 2, 13, 0.06, 0.00),
    "sai_cedo": (0.05, -6, 5, 0.55, 0.00),
    "intervalo": (0.05, -6, 6, 0.03, 0.60),
}
PERSONA_WEIGHTS = [("pontual", 60), ("atrasado", 10), ("faltoso", 8), ("irregular", 12), ("sai_cedo", 6),
                   ("intervalo", 4)]
# Turmas no padrão FIAP: {ano}{curso}{turno} — EC = Engenharia de Computação; A manhã, T tarde, R noite.
CLASSES = [("1ECA", "Manhã"), ("2ECA", "Manhã"), ("1ECT", "Tarde"), ("2ECT", "Tarde"), ("1ECR", "Noite"),
           ("2ECR", "Noite")]
WORST_CLASS = "1ECT"          # turma visivelmente pior em pontualidade
RM_BASE = 550000              # RMs fictícios: RM550001, RM550002… (padrão FIAP: RM + 6 números)
WORST_CLASS_DELAY = 17
JUSTIFY_REASONS = ["Atestado médico", "Consulta médica", "Atestado odontológico", "Falecimento na família",
                   "Viagem autorizada pela coordenação", "Doença (declaração do responsável)"]


class SampleDataError(RuntimeError):
    pass


# ================================================================== estado
def get_state() -> Dict[str, Any]:
    """{'estado': 'nunca' | 'gerado' | 'removido', ...} + contagem atual de registros fictícios."""
    with get_db_connection() as conn:
        row = conn.execute("SELECT value FROM school_settings WHERE key = ?", (STATE_KEY,)).fetchone()
        counts = {
            "alunos": conn.execute("SELECT COUNT(*) FROM students WHERE ficticio = 1").fetchone()[0],
            "alertas": conn.execute("SELECT COUNT(*) FROM security_events WHERE ficticio = 1").fetchone()[0],
            "cameras": conn.execute("SELECT COUNT(*) FROM cameras WHERE ficticio = 1").fetchone()[0],
            "passagens": conn.execute("SELECT COUNT(*) FROM presences WHERE student_id IN "
                                      "(SELECT id FROM students WHERE ficticio = 1)").fetchone()[0],
        }
    state: Dict[str, Any] = {"estado": "nunca"}
    if row and row[0]:
        try:
            state.update(json.loads(row[0]))
        except ValueError:
            pass
    state["registros"] = counts
    state["auto"] = auto_enabled()
    return state


def _set_state(**values) -> None:
    with get_db_connection() as conn:
        conn.execute("INSERT INTO school_settings (key, value) VALUES (?, ?) "
                     "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                     (STATE_KEY, json.dumps(values, ensure_ascii=False)))
        conn.commit()


def auto_enabled() -> bool:
    return os.getenv("DADOS_FICTICIOS", "auto").strip().lower() not in ("off", "false", "0", "nao", "não", "no")


def should_seed() -> bool:
    """Popular sozinho só na 1ª vez: banco sem nenhum aluno e nunca populado antes."""
    if not auto_enabled():
        return False
    with get_db_connection() as conn:
        has_students = conn.execute("SELECT 1 FROM students LIMIT 1").fetchone() is not None
        row = conn.execute("SELECT value FROM school_settings WHERE key = ?", (STATE_KEY,)).fetchone()
    return not has_students and not (row and row[0])


# ================================================================== geração
class _Gen:
    def __init__(self, days: int, n_students: int, seed: int, now: datetime, quiet: bool):
        self.days, self.n, self.now, self.quiet, self.seed = days, n_students, now, quiet, seed
        self.rnd = random.Random(seed)
        self.rtg = random.Random(seed + 7)    # sorteios do Telegram simulado (não mexe na sequência das passagens)
        self.today = now.date()
        self.start = self.today - timedelta(days=days - 1)
        self.track = 1000
        self.passages: List[tuple] = []
        self.students: List[Dict[str, Any]] = []
        self.special: Dict[str, Any] = {}

    def log(self, msg: str) -> None:
        if not self.quiet:
            print(msg, flush=True)

    def _exec(self, sql: str, args: tuple = ()) -> None:
        with get_db_connection() as conn:
            conn.execute(sql, args)
            conn.commit()

    # ------------------------------------------------------------------ escola
    def school(self) -> None:
        from database import repository, school
        settings = school.get_settings()
        # Ano letivo precisa cobrir o período simulado.
        ys = min(school.parse_day(settings["year_start"]), self.start - timedelta(days=1))
        ye = max(school.parse_day(settings["year_end"]), self.today)
        school.update_settings({"year_start": ys.isoformat(), "year_end": ye.isoformat()})
        shifts = {s["name"]: s for s in school.list_shifts()}
        missing = [sh for _, sh in CLASSES if sh not in shifts]
        if missing:
            raise SampleDataError(f"Turno(s) {', '.join(sorted(set(missing)))} não encontrado(s) em Escola → Turnos.")
        for name, shift in CLASSES:
            c = school.get_class_by_name(name)
            if c is None:
                c = school.create_class(name, shifts[shift]["id"])
                self._exec("UPDATE classes SET ficticio = 1 WHERE id = ?", (c["id"],))
        self.shifts = shifts

        sat = self.today - timedelta(days=(self.today.weekday() - 5) % 7 + 21)
        if sat >= self.start:
            e = school.create_calendar_entry(sat.isoformat(), "", "DIA_LETIVO", "Sábado letivo de reposição (fictício)",
                                             shift_id=shifts["Manhã"]["id"])
            self._exec("UPDATE calendar_entries SET ficticio = 1 WHERE id = ?", (e["id"],))
            self.special["sabado_letivo"] = sat
        council = self._weekday_near(self.today - timedelta(days=23))
        e = school.create_calendar_entry(council.isoformat(), "", "SEM_AULA", "Conselho de classe (fictício)",
                                         shift_id=shifts["Noite"]["id"])
        self._exec("UPDATE calendar_entries SET ficticio = 1 WHERE id = ?", (e["id"],))
        self.special["conselho"] = council

        # Sem câmeras de exemplo: a escola cadastra as reais na tela (fiel ao uso na feira).
        # As passagens fictícias usam a câmera principal (a do realguard.env).
        self.cam_main = int(os.getenv("CAMERA_ID", "1") or 1)
        self.cam_exit = self.cam_main

    def _free_rm(self, n: int) -> str:
        """RM fictício no padrão FIAP que não colide com nenhum aluno já cadastrado."""
        from database import repository
        k = RM_BASE + n
        while repository.get_student_by_registration(f"RM{k:06d}"):
            k += 1000
        return f"RM{k:06d}"

    @staticmethod
    def _weekday_near(d: date) -> date:
        while d.weekday() >= 5:
            d -= timedelta(days=1)
        return d

    # ------------------------------------------------------------------ alunos
    def people(self) -> None:
        from database import repository
        used = set()
        per_class = math.ceil(self.n / len(CLASSES))
        personas = [p for p, w in PERSONA_WEIGHTS for _ in range(w)]
        for i in range(self.n):
            cls = CLASSES[i // per_class % len(CLASSES)][0]
            while True:
                name = f"{self.rnd.choice(FIRST)} {self.rnd.choice(LAST)} {self.rnd.choice(LAST)}"
                if name not in used:
                    used.add(name)
                    break
            last = name.split()[-1]
            guardian = f"{self.rnd.choice(GUARDIAN_FIRST)} {last}"
            phone = f"(11) 90000-{i:04d}"                    # faixa obviamente fictícia
            rm = self._free_rm(i + 1)
            sid = repository.create_student(name, rm, "", cls, "", "")
            if not sid:
                raise SampleDataError(f"Não consegui criar o aluno fictício {rm}.")
            # Responsáveis fictícios. Mãe/pai e, às vezes, avó/tio. A maioria tem Telegram SIMULADO
            # (chat "fic-…": o notificador finge o envio e nada sai para a internet — ver telegram.is_simulated).
            from database import guardians as gdb
            first_name = guardian.split()[0]
            tg = self.rtg.random() < 0.85
            gid = gdb.create(guardian, phone, chat_id=f"fic-{i + 1:03d}" if tg else "", ficticio=True)
            gdb.link(sid, gid, "Mãe" if first_name in WOMEN else "Pai", primary=True)
            if self.rnd.random() < 0.35:
                rel, names = self.rnd.choice(SECOND)
                g2 = gdb.create(f"{self.rnd.choice(names)} {last}", f"(11) 90001-{i:04d}",
                                chat_id=f"fic-{i + 1:03d}b" if tg and self.rtg.random() < 0.5 else "", ficticio=True)
                gdb.link(sid, g2, rel)
            persona = self.rnd.choice(personas)
            created = date(self.start.year, 2, 1) if self.start.month > 2 else self.start - timedelta(days=30)
            novato = i % 20 == 7
            if novato:
                created = self.start + timedelta(days=self.rnd.randint(self.days // 4, self.days * 3 // 4))
            consent = not (i % 12 == 5)
            self._exec("UPDATE students SET created_at = ?, ficticio = 1 WHERE id = ?",
                       (datetime.combine(created, dtime(8, 0)).strftime(FMT), sid))
            if consent:
                repository.set_consent(sid, guardian, ACTOR["full_name"], 1)
                self._exec("UPDATE students SET consent_at = ? WHERE id = ?",
                           (datetime.combine(created, dtime(9, 30)).strftime(FMT), sid))
            self.students.append({"id": sid, "name": name, "rm": rm, "class": cls, "persona": persona,
                                  "created": created, "novato": novato, "consent": consent, "tg": tg})
        self.log(f"  {len(self.students)} alunos fictícios em {len(CLASSES)} turmas")

    # ------------------------------------------------------------------ passagens
    def _ts(self, d: date, minutes: float) -> datetime:
        minutes = max(0, min(int(round(minutes)), 23 * 60 + 58))
        return datetime.combine(d, dtime(minutes // 60, minutes % 60, self.rnd.randint(0, 59)))

    def _add(self, s: Dict[str, Any], when: datetime, direction: str, camera: int) -> None:
        if when > self.now:
            return
        self.track += 1
        self.passages.append((s["id"], s["name"], s["rm"], camera, self.track, round(self.rnd.uniform(0.5, 0.82), 3),
                              direction, "SENT" if s.get("tg") else "NO_CHAT_ID",
                              "Envio simulado (dado fictício)." if s.get("tg") else "Registro fictício (sem Telegram).",
                              when.strftime(FMT)))

    def passages_for_period(self) -> None:
        from database import school
        cal = school.Calendar.load()
        days = [self.start + timedelta(days=k) for k in range(self.days)]
        weekdays = [d for d in days if d < self.today and d.weekday() < 5
                    and all(cal.day_status(d, s["id"])[0] for s in self.shifts.values())]
        self.special["chuva"] = sorted(self.rnd.sample(weekdays, 2)) if len(weekdays) >= 2 else []
        off = [d for d in weekdays if (self.today - d).days in range(14, 25) and d not in self.special["chuva"]]
        self.special["camera_fora"] = off[0] if off else None
        self.off_day_present: List[Tuple[Dict[str, Any], str]] = []

        for d in days:
            for s in self.students:
                if d < s["created"]:
                    continue
                shift = cal.shift_for_class(s["class"])
                if not shift or not cal.day_status(d, shift["id"])[0]:
                    continue
                p_abs, mu, sd, p_early, p_break = PERSONAS[s["persona"]]
                if d.weekday() in (0, 4):
                    p_abs *= 1.6
                if self.rnd.random() < p_abs:
                    continue
                start = school.minutes(shift["start_time"])
                end = school.minutes(shift["end_time"])
                delay = self.rnd.gauss(mu, sd) + (WORST_CLASS_DELAY if s["class"] == WORST_CLASS else 0)
                if d in self.special["chuva"]:
                    delay += abs(self.rnd.gauss(14, 6))
                arrive = max(start - 35, start + delay)
                if d == self.special["camera_fora"] and shift["name"] == "Manhã":
                    self.off_day_present.append((s, school.hhmm(int(arrive))))
                    continue
                self._add(s, self._ts(d, arrive), "ENTRY", self.cam_main)
                if self.rnd.random() < p_break:
                    mid = (start + end) / 2 + self.rnd.gauss(0, 20)
                    self._add(s, self._ts(d, mid), "EXIT", self.cam_main)
                    self._add(s, self._ts(d, mid + self.rnd.uniform(20, 45)), "ENTRY", self.cam_main)
                if self.rnd.random() < p_early:
                    leave = end - self.rnd.uniform(35, 120)
                else:
                    leave = end + abs(self.rnd.gauss(4, 4))
                self._add(s, self._ts(d, leave), "EXIT", self.cam_exit if self.rnd.random() < 0.7 else self.cam_main)
        with get_db_connection() as conn:
            conn.executemany("INSERT INTO presences (student_id, student_name, registration_number, camera_id, "
                             "track_id, similarity, direction, telegram_status, telegram_detail, created_at) "
                             "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", self.passages)
            conn.commit()
        self.log(f"  {len(self.passages)} passagens de câmera em {self.days} dias")

    # ------------------------------------------------------------------ consolidação + correções
    def consolidate_and_fix(self) -> None:
        from database import attendance
        attendance.recompute_range(self.start, self.today, now=self.now)
        attendance.close_pending_days(now=self.now)

        off = self.special.get("camera_fora")
        for s, hhmm in self.off_day_present:
            try:
                attendance.mark_present(s["id"], off, hhmm, ACTOR, "Câmera fora do ar — registro pela portaria",
                                        now=self.now)
                self._backdate_manual(s["id"], off, datetime.combine(off, dtime(10, self.rnd.randint(0, 59))))
            except attendance.AttendanceError:
                pass

        with get_db_connection() as conn:
            absences = conn.execute(
                "SELECT a.student_id, a.day FROM attendance_days a JOIN students s ON s.id = a.student_id "
                "WHERE s.ficticio = 1 AND a.status = 'AUSENTE' AND a.day < ? ORDER BY a.day, a.student_id",
                (self.today.isoformat(),)).fetchall()
        absences = [tuple(r) for r in absences]
        picked = self.rnd.sample(absences, min(len(absences), 28))
        for sid, day in picked:
            d = date.fromisoformat(day)
            attendance.justify(sid, d, self.rnd.choice(JUSTIFY_REASONS), ACTOR, now=self.now)
            when = datetime.combine(min(d + timedelta(days=self.rnd.randint(1, 3)), self.today - timedelta(days=1)),
                                    dtime(self.rnd.randint(8, 16), self.rnd.randint(0, 59)))
            self._backdate_manual(sid, d, max(when, datetime.combine(d, dtime(18, 0))))
        self.special["justificadas"] = len(picked)
        self.special["manuais"] = len(self.off_day_present)
        self.log(f"  {len(picked)} faltas justificadas e {len(self.off_day_present)} presenças manuais")

    def _backdate_manual(self, sid: int, d: date, when: datetime) -> None:
        self._exec("UPDATE attendance_days SET manual_at = ? WHERE student_id = ? AND day = ?",
                   (when.strftime(FMT), sid, d.isoformat()))

    # ------------------------------------------------------------------ alertas
    def alerts(self) -> None:
        from database import repository, school
        cal = school.Calendar.load()
        events = []
        for k in range(self.days):
            d = self.start + timedelta(days=k)
            if d >= self.today or not cal.day_status(d, self.shifts["Manhã"]["id"])[0]:
                continue
            for _ in range(2 if self.rnd.random() < 0.15 else (1 if self.rnd.random() < 0.4 else 0)):
                shift = self.rnd.choice(list(self.shifts.values()))
                minute = school.minutes(shift["start_time"]) + self.rnd.randint(-25, 40)
                r = self.rnd.random()
                status = "VALIDATED_TRUE" if r < 0.22 else "VALIDATED_FALSE" if r < 0.68 else "AUTO_DISMISSED"
                events.append((self._ts(d, minute), status))
        now_min = self.now.hour * 60 + self.now.minute
        if cal.day_status(self.today, self.shifts["Manhã"]["id"])[0]:
            for back in (47, 9):
                events.append((self._ts(self.today, max(1, now_min - back)), "PENDING"))
        photo = _snapshot_jpeg()
        with get_db_connection() as conn:     # quem decidiu: usuários que já existem (nunca cria login fictício)
            deciders = [r[0] for r in conn.execute(
                "SELECT id FROM users WHERE active = 1 AND COALESCE(password_hash,'') != ''")]
        for created, status in events:
            created = min(created, self.now)
            eid = repository.save_security_event({"event_type": "FACE_OBSTRUCTED", "camera_id": self.cam_main,
                                                  "severity": "RED", "track_id": self.rnd.randint(1, 900),
                                                  "description": "Rosto obstruído (fictício)",
                                                  "detection_confidence": round(self.rnd.uniform(0.55, 0.9), 2),
                                                  "status": "PENDING"}, return_id=True)
            if not eid:
                continue
            decided = created + timedelta(minutes=self.rnd.randint(1, 15))
            cat, note = ("", "")
            if status == "VALIDATED_TRUE":
                cat, note = self.rnd.choice([("nao_identificado", "Pessoa de capuz, não era aluno"),
                                             ("suspeito", "Ficou parada no portão por vários minutos"),
                                             ("acesso_indevido", "Tentou entrar junto com um aluno"),
                                             ("nao_identificado", "Entregador sem identificação")])
            self._exec("UPDATE security_events SET created_at = ?, status = ?, validated_by = ?, validated_at = ?, "
                       "description = ?, category = ?, note = ?, decided_via = ?, ficticio = 1 WHERE id = ?",
                       (created.strftime(FMT), status,
                        self.rnd.choice(deciders) if deciders and status in ("VALIDATED_TRUE", "VALIDATED_FALSE") else None,
                        decided.strftime(FMT) if status != "PENDING" else None,
                        "Rosto obstruído (fictício)" + (" [descartado automaticamente: rosto apareceu]"
                                                         if status == "AUTO_DISMISSED" else ""), cat, note,
                        (("telegram" if self.rnd.random() < 0.45 else "painel")
                         if status in ("VALIDATED_TRUE", "VALIDATED_FALSE") else ""), eid))
            if (self.now - created).days < 30 and photo:      # retenção: fotos só dos últimos 30 dias
                repository.save_event_snapshot(eid, photo)
        self.special["alertas"] = len(events)
        self.log(f"  {len(events)} alertas de segurança fictícios")


def _snapshot_jpeg() -> Optional[bytes]:
    """Imagem genérica de alerta, marcada como FICTÍCIA (sem rosto de ninguém)."""
    try:
        import cv2
        import numpy as np
        img = np.full((360, 640, 3), (42, 38, 34), dtype=np.uint8)
        cv2.rectangle(img, (270, 70), (370, 330), (90, 90, 90), -1)
        cv2.circle(img, (320, 70), 38, (70, 70, 70), -1)
        cv2.rectangle(img, (0, 320), (640, 360), (20, 20, 160), -1)
        cv2.putText(img, "FICTICIO - RealGuard", (190, 348), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 70])
        return buf.tobytes() if ok else None
    except Exception:
        return None


def _pickups(self) -> None:
    """Algumas retiradas autorizadas (hoje, amanhã e uma aguardando o principal), sem foto e sem Telegram."""
    picks = [(0, "ACTIVE", "Rosa", "Avó"), (0, "ACTIVE", "Carlos", "Tio"), (0, "PENDING", "Fernanda", "Tia"),
             (1, "ACTIVE", "Marta", "Madrinha"),
             (1, "PENDING", "Jorge", "Vizinho(a)"), (-3, "ACTIVE", "Helena", "Tia")]
    alvo = [st for st in self.students if not st["novato"]][:len(picks)]
    with get_db_connection() as conn:
        for st, (delta, status, first, rel) in zip(alvo, picks):
            gs = conn.execute("SELECT guardian_id, is_primary FROM student_guardians WHERE student_id = ? "
                              "ORDER BY is_primary DESC", (st["id"],)).fetchall()
            if not gs:
                continue
            req = gs[0][0] if status == "ACTIVE" or len(gs) == 1 else gs[-1][0]
            day = (self.today + timedelta(days=delta)).isoformat()
            conn.execute("INSERT INTO pickup_authorizations (student_id, day, person_name, relationship, status, "
                         "requested_by, decided_by, decided_at, ficticio) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)",
                         (st["id"], day, f"{first} {st['name'].split()[-1]}", rel, status, req,
                          gs[0][0] if status == "ACTIVE" else None,
                          self.now.strftime(FMT) if status == "ACTIVE" else None))
        conn.commit()
    self.log("  retiradas autorizadas fictícias (hoje e amanhã)")


_Gen.pickups = _pickups


def _notifications(self) -> None:
    """Histórico de avisos aos responsáveis das últimas 2 semanas, para quem tem Telegram simulado."""
    from database import school
    cal = school.Calendar.load()
    since = (self.today - timedelta(days=13)).isoformat()
    klass = {s["id"]: s["class"] for s in self.students}
    with get_db_connection() as conn:
        chats: Dict[int, List[Tuple[str, str]]] = {}
        for r in conn.execute("SELECT sg.student_id, g.telegram_chat_id, g.name, sg.relationship FROM student_guardians sg "
                              "JOIN guardians g ON g.id = sg.guardian_id WHERE g.telegram_chat_id LIKE 'fic-%' "
                              "ORDER BY sg.is_primary DESC"):
            chats.setdefault(r[0], []).append((r[1], f"{r[2]} ({r[3]})" if r[3] else r[2]))
        days = conn.execute("SELECT a.student_id, s.name, a.day, a.deadline, a.status, a.early_exit, a.cam_exit, "
                            "a.cam_entry "
                            "FROM attendance_days a JOIN students s ON s.id = a.student_id "
                            "WHERE s.ficticio = 1 AND a.day >= ?", (since,)).fetchall()
    rows: List[tuple] = []

    def add(sid, name, kind, when, sent_by=""):
        if when > self.now:
            return
        for chat, label in chats.get(sid, []):
            failed = self.rtg.random() < 0.02
            rows.append((sid, name, label, chat, kind, "FAILED" if failed else "SENT",
                         "Forbidden: bot was blocked by the user" if failed else "", sent_by,
                         (when + timedelta(seconds=self.rtg.randint(1, 9))).strftime(FMT)))

    for sid, name, _rm, _cam, _track, _sim, direction, _st, _det, ts in self.passages:
        if ts[:10] < since or sid not in chats:
            continue
        when = datetime.strptime(ts, FMT)
        kind = "EXIT"
        if direction == "ENTRY":
            shift = cal.shift_for_class(klass.get(sid, ""))
            kind = "LATE" if shift and when.hour * 60 + when.minute > school.minutes(shift["deadline"]) else "ENTRY"
        add(sid, name, kind, when)
    try:   # aviso automático de ausência (Escola → Regras); 0 = a portaria avisou na mão
        auto = max(0, int(school.get_settings().get("absence_notice_after_minutes", 60)))
    except (TypeError, ValueError):
        auto = 60
    for sid, name, day, deadline, status, early, cam_exit, cam_entry in days:
        d = date.fromisoformat(day)
        missed = status == "AUSENTE" or (status == "JUSTIFICADA" and not cam_entry)   # avisado antes da justificativa
        if not missed or d == self.today:      # hoje: quem avisa é a rotina automática, na hora certa
            continue
        if auto:
            add(sid, name, "ABSENCE", datetime.combine(d, dtime(0, 0)) + timedelta(
                minutes=school.minutes(deadline) + auto), "")
        elif self.rtg.random() < 0.7:
            add(sid, name, "ABSENCE", datetime.combine(d, dtime(0, 0)) + timedelta(
                minutes=school.minutes(deadline) + self.rtg.randint(15, 40)), "Portaria")
    with get_db_connection() as conn:
        conn.executemany("INSERT INTO notification_log (student_id, student_name, guardian, chat_id, kind, status, "
                         "detail, sent_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
        conn.commit()
    self.log(f"  {len(rows)} avisos fictícios aos responsáveis (Telegram simulado)")


_Gen.notifications = _notifications


# Gerar/remover e a simulação ao vivo (app/simulation.py) não podem rodar ao mesmo tempo.
LOCK = __import__("threading").RLock()


def generate(days: int = 90, students: int = 120, seed: int = 42, now: Optional[datetime] = None,
             quiet: bool = False) -> Dict[str, Any]:
    """Acrescenta os dados fictícios ao banco atual. Não mexe em nada que já exista (só cria)."""
    with LOCK:
        return _generate(days, students, seed, now, quiet)


def _generate(days: int, students: int, seed: int, now: Optional[datetime], quiet: bool) -> Dict[str, Any]:
    if days < 7 or students < 6:
        raise ValueError("Use pelo menos 7 dias e 6 alunos.")
    g = _Gen(days, students, seed, (now or datetime.now()).replace(microsecond=0), quiet)
    g.log(f"Criando dados fictícios ({students} alunos, {days} dias)...")
    g.school()
    g.people()
    g.passages_for_period()
    g.consolidate_and_fix()
    g.alerts()
    g.pickups()
    g.notifications()
    info = {"estado": "gerado", "em": g.now.strftime(FMT), "de": g.start.isoformat(), "ate": g.today.isoformat(),
            "dias": days, "alunos": students, "semente": seed}
    _set_state(**info)
    try:            # a simulação ao vivo continua daqui (sem repetir o que acabou de ser gerado)
        from app.simulation import set_state as _sim
        _sim(ate=info["em"])
    except Exception as e:
        logger.warning(f"Simulação ao vivo não ajustada: {e}")
    return info


# ================================================================== remoção
def remove(keep_camera_id: Optional[int] = None) -> Dict[str, int]:
    """Apaga SÓ o que tem ficticio = 1 (e o que depende disso). Alunos e câmeras reais ficam intactos."""
    with LOCK:
        return _remove(keep_camera_id)


def _remove(keep_camera_id: Optional[int] = None) -> Dict[str, int]:
    from database import repository
    keep = int(keep_camera_id if keep_camera_id is not None else (os.getenv("CAMERA_ID", "1") or 1))
    with get_db_connection() as conn:
        ids = [r[0] for r in conn.execute("SELECT id FROM students WHERE ficticio = 1")]
        events = [r[0] for r in conn.execute("SELECT id FROM security_events WHERE ficticio = 1")]
        cams = [r[0] for r in conn.execute("SELECT id FROM cameras WHERE ficticio = 1 AND id != ?", (keep,))]
        n = {"alunos": len(ids), "alertas": len(events), "cameras": len(cams)}
        sub = "SELECT id FROM students WHERE ficticio = 1"
        n["passagens"] = conn.execute(f"DELETE FROM presences WHERE student_id IN ({sub})").rowcount
        conn.execute(f"DELETE FROM attendance_days WHERE student_id IN ({sub})")
        conn.execute(f"DELETE FROM notification_log WHERE student_id IN ({sub})")
        from database import guardians as gdb
        for sid in ids:
            gdb.remove_student(conn, sid)
            from database import pickups as _pk
            _pk.remove_student(conn, sid)
        conn.execute(f"DELETE FROM student_faces WHERE student_id IN ({sub})")
        conn.execute("DELETE FROM security_events WHERE ficticio = 1")
        conn.execute("DELETE FROM students WHERE ficticio = 1")
        conn.execute("DELETE FROM calendar_entries WHERE ficticio = 1")
        for cid in cams:
            conn.execute("DELETE FROM presences WHERE camera_id = ? AND student_id IS NULL", (cid,))
            conn.execute("DELETE FROM zones WHERE camera_id = ?", (cid,))
            conn.execute("DELETE FROM rules WHERE camera_id = ?", (cid,))
            conn.execute("DELETE FROM cameras WHERE id = ?", (cid,))
        conn.execute("UPDATE cameras SET ficticio = 0 WHERE id = ?", (keep,))
        conn.execute("DELETE FROM classes WHERE ficticio = 1 AND name NOT IN "
                     "(SELECT DISTINCT class_name FROM students WHERE class_name IS NOT NULL)")
        conn.commit()
    for eid in events:
        try:
            os.remove(repository.event_snapshot_path(eid))
        except OSError:
            pass
    prev = get_state()
    _set_state(estado="removido", em=datetime.now().strftime(FMT), antes=prev.get("em", ""))
    return n


def regenerate(now: Optional[datetime] = None) -> Dict[str, Any]:
    """Remove os fictícios e cria de novo até hoje (mesmo tamanho da última geração)."""
    prev = get_state()
    remove()
    return generate(days=int(prev.get("dias") or 90), students=int(prev.get("alunos") or 120),
                    seed=int(prev.get("semente") or 42), now=now, quiet=True)
