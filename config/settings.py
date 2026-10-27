"""
Configuração central do RealGuard.

Toda configuração vem de variáveis de ambiente, com este arquivo como
fonte dos valores padrão. Para não precisar exportar nada no terminal,
o arquivo `config/realguard.env` (local, NÃO versionado) é carregado
automaticamente ao importar este módulo — igual no Windows e na AIBOX.

Prioridade: variável de ambiente do processo > config/realguard.env > padrão.
"""

import os
import logging
from typing import Any, Dict
from urllib.parse import quote

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_DIR = os.path.join(BASE_DIR, "config")
ENV_FILE = os.path.join(CONFIG_DIR, "realguard.env")
WEB_DIR = os.path.join(BASE_DIR, "web")
DEFAULT_DB_PATH = os.path.join(BASE_DIR, "data", "realguard.db")
DEFAULT_MODEL_PATH = os.path.join(BASE_DIR, "models", "yolov8n.onnx")


def _load_env_file(path: str = ENV_FILE) -> bool:
    """
    Lê um arquivo KEY=VALUE simples (sem depender de python-dotenv) e
    preenche os.environ SEM sobrescrever o que já estiver definido.
    """
    if not os.path.isfile(path):
        return False
    with open(path, "r", encoding="utf-8-sig") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key, value = key.strip(), value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
                value = value[1:-1]
            os.environ.setdefault(key, value)
    return True


ENV_FILE_LOADED = _load_env_file()
# Set before numpy/OpenCV are imported; session pools are configured separately.
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("OPENCV_FOR_THREADS_NUM", "1")


# ------------------------------------------------------------------ fuso horário
# Entrada, saída, atraso, turnos e a simulação dos alunos fictícios usam o relógio LOCAL do
# processo. A AIBOX costuma vir de fábrica em UTC (ou no fuso da China): às 21h em Brasília ela
# "acha" que já é madrugada/manhã do dia seguinte. Por isso o RealGuard fixa o próprio fuso,
# sem mexer no sistema (sem sudo). FUSO_HORARIO=sistema mantém o fuso do equipamento.
DEFAULT_TIMEZONE = "America/Sao_Paulo"
_TZ_SEM_TZDATA = {"America/Sao_Paulo": "<-03>3", "America/Fortaleza": "<-03>3", "America/Recife": "<-03>3",
                  "America/Bahia": "<-03>3", "America/Belem": "<-03>3", "America/Manaus": "<-04>4",
                  "America/Cuiaba": "<-04>4", "America/Porto_Velho": "<-04>4", "America/Rio_Branco": "<-05>5"}


# Horário da demonstração (feira das 12h às 15h, mas mostrando o turno da noite): RELOGIO_AJUSTE_MIN
# adianta/atrasa o relógio do RealGuard inteiro (presenças, atrasos, turnos, simulação, relatórios).
# É feito pelo fuso do processo, então não muda o relógio do equipamento nem precisa de sudo.
CLOCK_ADJUST_KEY = "RELOGIO_AJUSTE_MIN"
CLOCK_DATE_KEY = "RELOGIO_AJUSTE_DIAS"
_clock_base = {"name": "do sistema", "tz": None}


def clock_adjust_minutes() -> int:
    try:
        return max(-1439, min(1439, int(float(os.getenv(CLOCK_ADJUST_KEY, "0") or 0))))
    except ValueError:
        return 0


def clock_adjust_days() -> int:
    try:
        return max(-3650, min(3650, int(os.getenv(CLOCK_DATE_KEY, "0") or 0)))
    except ValueError:
        return 0


def apply_timezone() -> str:
    """Aplica FUSO_HORARIO (+ RELOGIO_AJUSTE_MIN) ao processo (Linux). Devolve o nome, para o terminal mostrar."""
    import time
    name = (os.getenv("FUSO_HORARIO") or DEFAULT_TIMEZONE).strip()
    if not hasattr(time, "tzset"):                   # Windows não tem tzset: usa o relógio do PC como está
        return "do sistema"
    if name.lower() in ("sistema", "system"):
        tz, name = _clock_base["tz"] if _clock_base["tz"] is not None else os.environ.get("TZ", ""), "do sistema"
    else:
        tz = name
        if "/" in name and not os.path.exists(os.path.join("/usr/share/zoneinfo", name)):
            tz = _TZ_SEM_TZDATA.get(name, name)      # equipamento sem a base de fusos (tzdata)
    if _clock_base["tz"] is None:
        _clock_base["tz"] = os.environ.get("TZ", "")
    _set_tz(tz)
    adjust = clock_adjust_minutes()
    if adjust:
        east = time.localtime().tm_gmtoff // 60 + adjust          # minutos a leste de UTC, já ajustado
        sign = "-" if east > 0 else ""                            # POSIX: o sinal é invertido
        _set_tz(f"<RGD>{sign}{abs(east) // 60}:{abs(east) % 60:02d}")
    _clock_base["name"] = name
    return name


def _set_tz(tz: str) -> None:
    import time
    if tz:
        os.environ["TZ"] = tz
    else:
        os.environ.pop("TZ", None)
    time.tzset()


def clock_info() -> Dict[str, Any]:
    import time
    from datetime import datetime as RealDateTime, timedelta
    from config.clock import datetime

    minutes, days = clock_adjust_minutes(), clock_adjust_days()
    # Em Linux o deslocamento de minutos já está no TZ; no Windows ele é
    # aplicado somente pelo relógio de negócio. O equipamento nunca é alterado.
    real = RealDateTime.now() - timedelta(minutes=minutes if hasattr(time, "tzset") else 0)
    current = datetime.now()
    return {"fuso": _clock_base["name"], "ajuste_min": minutes, "ajuste_dias": days,
            "agora": current.strftime("%H:%M"), "data": current.date().isoformat(),
            "real": real.strftime("%H:%M"), "data_real": real.date().isoformat()}


def set_demo_time(hhmm: str = "", day: str = "") -> Dict[str, Any]:
    """Define data/hora virtuais sem alterar o relógio do equipamento.

    O deslocamento é persistido: após reiniciar, o relógio continua avançando.
    Sem data e sem horário, retorna a data e hora reais.
    """
    import time
    from datetime import date as RealDate, datetime as RealDateTime, timedelta

    hhmm, day = (hhmm or "").strip(), (day or "").strip()
    previous_minutes = clock_adjust_minutes()
    real = RealDateTime.now() - timedelta(minutes=previous_minutes if hasattr(time, "tzset") else 0)
    minutes, days = 0, 0
    if hhmm or day:
        if hhmm:
            try:
                parts = hhmm.replace("h", ":").split(":")
                if len(parts) == 1:  # compatibilidade com a API anterior: "20" = 20:00
                    parts.append("0")
                if len(parts) != 2:
                    raise ValueError
                hour, minute = (int(part) for part in parts)
                if not (0 <= hour < 24 and 0 <= minute < 60):
                    raise ValueError
            except ValueError:
                raise ValueError("Horário inválido. Use HH:MM, por exemplo 20:00.")
        else:
            hour, minute = real.hour, real.minute
        try:
            target_day = RealDate.fromisoformat(day) if day else real.date()
        except ValueError:
            raise ValueError("Data inválida. Use AAAA-MM-DD.")
        days = (target_day - real.date()).days
        if not -3650 <= days <= 3650:
            raise ValueError("A data deve estar a no máximo 10 anos da data real.")
        minutes = hour * 60 + minute - real.hour * 60 - real.minute

    # Primeiro desfaz o ajuste anterior do TZ para evitar acumulação no Linux.
    os.environ[CLOCK_ADJUST_KEY] = "0"
    apply_timezone()
    updates = {CLOCK_ADJUST_KEY: str(minutes) if minutes else "",
               CLOCK_DATE_KEY: str(days) if days else ""}
    from config import setup_wizard
    setup_wizard.update_env_file(ENV_FILE, updates,
                                 example_path=os.path.join(CONFIG_DIR, "realguard.env.example"))
    os.environ[CLOCK_ADJUST_KEY] = str(minutes)
    os.environ[CLOCK_DATE_KEY] = str(days)
    apply_timezone()
    return clock_info()


TIMEZONE = apply_timezone()


def _bool(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on")


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)).strip())
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)).strip())
    except ValueError:
        return default


def resolve_camera_source() -> str:
    """
    Decide a fonte de vídeo:
      1. RTSP_URL, se definida (compatibilidade; "0" = webcam índice 0)
      2. CAMERA_MODE=rtsp -> monta a URL com CAMERA_USER/PASS/IP/PORT
      3. padrão: webcam local (CAMERA_INDEX)
    """
    explicit = os.getenv("RTSP_URL", "").strip()
    if explicit:
        return explicit
    if os.getenv("CAMERA_MODE", "webcam").strip().lower() == "rtsp":
        user = os.getenv("CAMERA_USER", "")
        password = quote(os.getenv("CAMERA_PASS", ""), safe="")
        ip = os.getenv("CAMERA_IP", "")
        port = os.getenv("CAMERA_PORT", "554")
        path = os.getenv("CAMERA_RTSP_PATH", "/cam/realmonitor?channel=1&subtype=1")
        return f"rtsp://{user}:{password}@{ip}:{port}{path}"
    return str(_int("CAMERA_INDEX", 0))


# Força RTSP sobre TCP e baixa latência no FFmpeg (evita atraso acumulado).
os.environ.setdefault(
    "OPENCV_FFMPEG_CAPTURE_OPTIONS",
    "rtsp_transport;tcp|fflags;nobuffer|flags;low_delay|max_delay;500000",
)


def resolve_api_host(camera_mode: str, source: str) -> str:
    value = os.getenv("API_HOST", "auto").strip()
    if value and value.lower() != "auto":
        return value
    is_ip_camera = camera_mode == "rtsp" or not str(source).isdigit()
    return "0.0.0.0" if is_ip_camera else "127.0.0.1"


def lan_addresses(target: str = "192.168.50.108") -> list:
    """
    IPs desta máquina para montar o link do dashboard. O "connect" em UDP não
    envia nenhum pacote: só pergunta ao sistema qual interface usaria.
    """
    import socket
    found = []
    for probe in (target, "8.8.8.8"):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.connect((probe, 80))
                ip = sock.getsockname()[0]
                if ip and not ip.startswith("127.") and ip not in found:
                    found.append(ip)
        except OSError:
            continue
    return found


def resolve_insightface_root(model_name: str) -> str:
    """
    Pasta onde o InsightFace guarda/baixa os modelos. Por padrão fica DENTRO
    do projeto (models/insightface): apagar a pasta do projeto remove tudo.
    Se o pack já existir em ~/.insightface e ainda não houver cópia local
    (caso da AIBOX, sem internet), reaproveita o de lá.
    """
    explicit = os.getenv("INSIGHTFACE_ROOT", "").strip()
    if explicit:
        return explicit
    local = os.path.join(BASE_DIR, "models", "insightface")
    home = os.path.join(os.path.expanduser("~"), ".insightface")
    if (not os.path.isdir(os.path.join(local, "models", model_name))
            and os.path.isdir(os.path.join(home, "models", model_name))):
        return home
    return local


def is_webcam_source(camera_mode: str, source: str) -> bool:
    """Webcam do computador (índice numérico) x câmera IP (URL RTSP/arquivo)."""
    return str(source).strip().isdigit() and camera_mode != "rtsp"


def resolve_mirror(value: str, camera_mode: str, source: str) -> bool:
    """
    MIRROR_VIDEO:
      auto (padrão) / true — automático: espelha a WEBCAM (visão de espelho) e
                             mostra a CÂMERA IP como ela grava (sem inverter);
      always / sempre      — espelha sempre;
      false / never / nao  — nunca espelha.
    "true" conta como automático porque era o valor padrão das versões
    anteriores (quem já tem o realguard.env não precisa editar nada).
    """
    value = (value or "auto").strip().lower()
    if value in ("always", "sempre"):
        return True
    if value in ("false", "0", "no", "off", "never", "nunca", "nao", "não"):
        return False
    return is_webcam_source(camera_mode, source)


class Settings:
    """Snapshot das configurações, lido do ambiente no momento da criação."""

    def __init__(self):
        # Câmera / vídeo
        self.camera_id: int = _int("CAMERA_ID", 1)
        self.camera_name: str = os.getenv("CAMERA_NAME", "Camera Principal")
        self.rtsp_url: str = resolve_camera_source()
        self.camera_mode: str = os.getenv("CAMERA_MODE", "webcam").strip().lower()
        # Teto do loop principal (captura->tracker->zonas->regras->desenho->publica).
        # Antes fixo em 15.0 e NUNCA repassado pelo engine.py — ou seja, o sistema
        # nunca processava mais que 15 fps mesmo com a câmera entregando mais e a
        # CPU sobrando, porque o loop DORMIA de propósito pra respeitar esse teto
        # (ver RealGuardPipeline._run_loop / min_interval). Com a detecção e o
        # reconhecimento já rodando em threads próprias (assíncronos), o teto
        # real passa a ser a taxa da câmera — 30 aqui só evita o loop girar mais
        # rápido que qualquer câmera real entrega, sem limitar artificialmente.
        self.max_fps: float = _float("MAX_FPS", 60.0)
        # Câmera de muitos megapixels: a IA trabalha numa cópia reduzida a esta largura (0 = original).
        self.process_max_width: int = _int("PROCESS_MAX_WIDTH", 1280)
        # Espelhar o vídeo no dashboard (só exibição; a IA usa a imagem original).
        # Automático: webcam espelhada (como um espelho), câmera IP como ela grava.
        self.mirror_setting: str = os.getenv("MIRROR_VIDEO", "auto").strip().lower()
        self.mirror_video: bool = resolve_mirror(self.mirror_setting, self.camera_mode, self.rtsp_url)

        # Processamento (padrões = ajuste do EduVision validado na AIBOX)
        self.detection_interval_sec: float = _float("DETECTION_INTERVAL_SEC", 0.15)  # YOLO ~6-7x/s
        self.yolo_threads: int = _int("YOLO_THREADS", 2)
        self.yolo_confidence: float = _float("YOLO_CONFIDENCE", 0.3)
        self.face_threads: int = _int("FACE_THREADS", 2)
        self.recognition_interval_sec: float = _float("RECOGNITION_INTERVAL_SEC", 1.0)
        self.resolved_recheck_sec: float = _float("RESOLVED_RECHECK_SEC", 5.0)
        self.track_timeout_sec: float = _float("TRACK_TIMEOUT_SEC", 3.0)
        self.obstructed_min_sec: float = _float("OBSTRUCTED_MIN_SEC", 10.0)
        self.obstructed_min_attempts: int = _int("OBSTRUCTED_MIN_ATTEMPTS", 6)
        self.obstructed_min_person_height: float = _float("OBSTRUCTED_MIN_PERSON_HEIGHT", 0.25)

        # Persistência / modelos
        self.db_path: str = os.getenv("DB_PATH", DEFAULT_DB_PATH)
        model = os.getenv("MODEL_PATH", DEFAULT_MODEL_PATH)
        self.model_path: str = model if os.path.isabs(model) else os.path.join(BASE_DIR, model)
        self.use_face_recognition: bool = _bool("USE_FACE_RECOGNITION", "true")
        self.face_model: str = os.getenv("FACE_MODEL", "buffalo_sc")
        self.face_threshold: float = _float("FACE_THRESHOLD", 0.45)
        # Limiar "suave": rosto de lado entre este valor e FACE_THRESHOLD conta
        # como evidência fraca do aluno (só ajuda, nunca identifica sozinho).
        self.face_soft_threshold: float = _float("FACE_SOFT_THRESHOLD", 0.35)
        self.show_visualization: bool = _bool("SHOW_VISUALIZATION", "false")

        # Servidor
        # API_HOST=auto (padrão): câmera IP (AIBOX) => 0.0.0.0, o dashboard abre
        # direto pelo navegador do notebook (http://IP_DA_AIBOX:PORTA), sem túnel SSH;
        # webcam (PC) => 127.0.0.1, só a própria máquina. Um IP explícito é respeitado.
        self.api_host: str = resolve_api_host(self.camera_mode, self.rtsp_url)
        self.api_port: int = _int("API_PORT", 8000)
        # HTTPS pronto, mas DESLIGADO por padrão (fase acadêmica em http na rede local).
        # Para ligar: gere um certificado (scripts/gerar_certificado.sh) e preencha os dois.
        self.ssl_certfile: str = os.getenv("SSL_CERTFILE", "").strip()
        self.ssl_keyfile: str = os.getenv("SSL_KEYFILE", "").strip()

        # Presença + Telegram
        # Horário limite de entrada (HH:MM): depois dele, chegada = atraso e quem não veio = ausente.
        self.entry_tolerance: str = os.getenv("ENTRY_TOLERANCE", "19:35").strip()
        self.presence_cooldown_sec: float = _float("PRESENCE_COOLDOWN_SEC", 30.0)
        self.telegram_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        # Bot só da segurança (alertas de rosto obstruído). Vazio = usa o bot dos responsáveis.
        self.security_bot_token: str = os.getenv("TELEGRAM_SECURITY_BOT_TOKEN", "").strip()
        # Vazio = mensagem formatada padrão. Os textos simples antigos do .env.example
        # também caem no padrão, para quem copiou o arquivo de versões anteriores.
        template = os.getenv("TELEGRAM_MESSAGE_TEMPLATE", "").strip()
        self.telegram_template: str = "" if template.startswith("✅ {nome}") else template
        # Quem recebe alertas de segurança (rosto obstruído): Chat IDs separados por vírgula.
        # Pode ser o ID de um GRUPO do Telegram com o bot adicionado (ex.: -1001234567890).
        self.security_chat_ids = [c.strip() for c in os.getenv("SECURITY_CHAT_IDS", "").split(",") if c.strip()]
        self.security_cooldown_sec: float = _float("SECURITY_ALERT_COOLDOWN_SEC", 60.0
        )

    @property
    def https_enabled(self) -> bool:
        return bool(self.ssl_certfile and self.ssl_keyfile)

    def validate(self):
        """Valida integridade mínima das configurações obrigatórias."""
        errors = []
        if not self.rtsp_url:
            errors.append("Fonte de vídeo inválida ou ausente.")
        if self.camera_id < 0:
            errors.append("CAMERA_ID deve ser um inteiro positivo.")
        if not os.path.exists(self.model_path):
            errors.append(f"Modelo de IA não encontrado: {self.model_path}")
        if errors:
            raise ValueError(f"Erros de configuração inicial: {'; '.join(errors)}")

    def get_safe_summary(self) -> Dict[str, Any]:
        """Resumo das configurações com credenciais mascaradas (seguro p/ logs)."""
        safe_url = self.rtsp_url
        if "@" in safe_url:
            try:
                protocol, rest = safe_url.split("://", 1)
                _creds, host = rest.rsplit("@", 1)
                safe_url = f"{protocol}://***:***@{host}"
            except Exception:
                safe_url = "rtsp://***:***@hidden"
        return {
            "CAMERA_ID": self.camera_id,
            "CAMERA_SOURCE": safe_url,
            "DB_PATH": self.db_path,
            "MODEL_PATH": self.model_path,
            "USE_FACE_RECOGNITION": self.use_face_recognition,
            "FACE_MODEL": self.face_model,
            "MAX_FPS": self.max_fps,
            "PROCESS_MAX_WIDTH": self.process_max_width,
            "DETECTION_INTERVAL_SEC": self.detection_interval_sec,
            "YOLO_THREADS": self.yolo_threads,
            "FACE_THREADS": self.face_threads,
            "RECOGNITION_INTERVAL_SEC": self.recognition_interval_sec,
            "TRACK_TIMEOUT_SEC": self.track_timeout_sec,
            "OBSTRUCTED_MIN_SEC": self.obstructed_min_sec,
            "MIRROR_VIDEO": self.mirror_video,
            "TELEGRAM": "configurado" if self.telegram_token else "não configurado",
            "PRESENCE_COOLDOWN_SEC": self.presence_cooldown_sec,
            "HTTPS": "ligado" if self.https_enabled else "desligado",
        }


def check_env_file_permissions(path: str = ENV_FILE) -> str:
    """
    O realguard.env guarda a senha da câmera e o token do bot: só o dono deve
    ler. Se estiver aberto para outros usuários, corrige para 600 e avisa.
    Devolve uma mensagem ("" = tudo certo). No Windows não se aplica.
    """
    if os.name != "posix" or not os.path.isfile(path):
        return ""
    mode = os.stat(path).st_mode & 0o777
    if not mode & 0o077:
        return ""
    try:
        os.chmod(path, 0o600)
        return f"Permissão do {os.path.basename(path)} era {oct(mode)}; corrigida para 600 (só o dono lê)."
    except OSError as e:
        return (f"ATENÇÃO: {os.path.basename(path)} pode ser lido por outros usuários ({oct(mode)}) e não consegui "
                f"corrigir ({e}). Rode: chmod 600 {path}")
