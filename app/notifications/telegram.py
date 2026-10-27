"""
Envio de mensagens pela Bot API do Telegram.

Só usa a biblioteca padrão (urllib) — nenhuma dependência extra na AIBOX.

O envio NUNCA acontece no loop de vídeo: as mensagens entram numa fila e
uma thread própria faz as chamadas HTTP (que podem levar segundos ou
falhar), então a câmera/IA não engasgam quando o Telegram está lento.
"""

import json
import logging
import queue
import threading
import time
import uuid
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

API_URL = "https://api.telegram.org/bot{token}/{method}"

# callback(ok, detalhe)
ResultCallback = Callable[[bool, str], None]


SIMULATED_PREFIX = "fic-"


def is_simulated(chat_id) -> bool:
    """Chats dos responsáveis FICTÍCIOS ("fic-…"): o envio é simulado e nada sai para o Telegram."""
    return str(chat_id or "").startswith(SIMULATED_PREFIX)


class TelegramNotifier:
    def __init__(self, token: str = "", timeout: float = 8.0):
        self.token = (token or "").strip()
        self.timeout = timeout
        self._queue: "queue.Queue[Optional[Tuple[str, str, Optional[ResultCallback], Optional[bytes], Optional[str]]]]" = queue.Queue(maxsize=200)
        self._thread: Optional[threading.Thread] = None
        self._bot_username: Optional[str] = None
        self._bot_checked_at = 0.0

    @property
    def enabled(self) -> bool:
        return bool(self.token)

    def set_token(self, token: str) -> None:
        """Troca o bot sem reiniciar (configuração pela tela)."""
        self.token = (token or "").strip()
        self._bot_username = None
        self._bot_checked_at = 0.0

    def check_token(self, token: str) -> Tuple[bool, str]:
        """Testa um token SEM trocar o atual. Retorna (ok, @usuario_do_bot ou erro)."""
        probe = TelegramNotifier(token, timeout=self.timeout)
        ok, result = probe._call("getMe")
        if ok and isinstance(result, dict):
            return True, result.get("username") or ""
        return False, str(result)

    # ------------------------------------------------------------ HTTP
    def _call(self, method: str, payload: Optional[Dict[str, Any]] = None, raw: Optional[Tuple[bytes, str]] = None,
              timeout: Optional[float] = None) -> Tuple[bool, Any]:
        """Chama a Bot API. Retorna (ok, resultado_ou_mensagem_de_erro)."""
        if payload and is_simulated(payload.get("chat_id")):
            return True, {"message_id": 0, "simulado": True}     # chat de dado fictício: nunca vai à rede
        if not self.enabled:
            return False, "Token do Telegram não configurado."
        url = API_URL.format(token=self.token, method=method)
        data, ctype = raw if raw else (json.dumps(payload or {}).encode("utf-8"), "application/json")
        req = urllib.request.Request(url, data=data, headers={"Content-Type": ctype})
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            # A API responde 4xx com um JSON explicando o motivo.
            try:
                body = json.loads(e.read().decode("utf-8"))
                return False, body.get("description", f"HTTP {e.code}")
            except Exception:
                return False, f"HTTP {e.code}"
        except Exception as e:  # sem internet, DNS, timeout...
            return False, f"Falha de rede: {type(e).__name__}"
        if not body.get("ok"):
            return False, body.get("description", "Resposta inválida do Telegram.")
        return True, body.get("result")

    # ------------------------------------------------------------ API pública
    def send_message(self, chat_id: str, text: str, parse_mode: Optional[str] = None,
                     reply_markup: Optional[Dict[str, Any]] = None) -> Tuple[bool, str]:
        """Envio síncrono (usado pelo botão de teste e pelo bot). Retorna (ok, detalhe)."""
        if not chat_id:
            return False, "Aluno sem Telegram Chat ID."
        if is_simulated(chat_id):
            return True, "simulado (dado fictício)"
        payload = {"chat_id": str(chat_id), "text": text}
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if reply_markup:
            payload["reply_markup"] = reply_markup
        ok, result = self._call("sendMessage", payload)
        return (True, "enviado") if ok else (False, str(result))

    def send_photo(self, chat_id: str, jpeg: bytes, caption: str = "", parse_mode: Optional[str] = None,
                   reply_markup: Optional[Dict[str, Any]] = None) -> Tuple[bool, str]:
        """Envia a imagem (frame) com legenda, como mensagem da conversa."""
        if not chat_id:
            return False, "Sem Telegram Chat ID."
        if is_simulated(chat_id):
            return True, "simulado (dado fictício)"
        b = "----rg" + uuid.uuid4().hex
        def field(n, v):
            return f'--{b}\r\nContent-Disposition: form-data; name="{n}"\r\n\r\n{v}\r\n'.encode("utf-8")
        body = (field("chat_id", chat_id) + field("caption", caption[:1000])
                + (field("parse_mode", parse_mode) if parse_mode else b"")
                + (field("reply_markup", json.dumps(reply_markup)) if reply_markup else b"")
                + f'--{b}\r\nContent-Disposition: form-data; name="photo"; filename="frame.jpg"\r\nContent-Type: image/jpeg\r\n\r\n'.encode()
                + jpeg + f"\r\n--{b}--\r\n".encode())
        ok, result = self._call("sendPhoto", raw=(body, f"multipart/form-data; boundary={b}"))
        return (True, "enviado") if ok else (False, str(result))

    PHOTO_MAX_BYTES = 5 * 1024 * 1024

    def download_photo(self, file_id: str, max_side: int = 960) -> bytes:
        """
        Baixa uma foto enviada ao bot e devolve um JPEG NOVO (reduzido e regravado):
        a regravação descarta os metadados do celular (EXIF, localização). Só baixa do
        próprio Telegram, confere o caminho e o tamanho. Erro = ValueError com texto para o usuário.
        """
        import re
        ok, info = self._call("getFile", {"file_id": str(file_id)})
        if not ok or not isinstance(info, dict):
            raise ValueError("Não consegui pegar a foto. Tente enviar de novo.")
        path = str(info.get("file_path") or "")
        if not re.fullmatch(r"[A-Za-z0-9_/.-]+", path) or ".." in path:
            raise ValueError("Arquivo de foto inválido.")
        if int(info.get("file_size") or 0) > self.PHOTO_MAX_BYTES:
            raise ValueError("A foto passa de 5 MB. Envie uma menor.")
        try:
            url = f"https://api.telegram.org/file/bot{self.token}/{path}"
            with urllib.request.urlopen(url, timeout=self.timeout * 2) as resp:
                data = resp.read(self.PHOTO_MAX_BYTES + 1)
        except Exception:
            raise ValueError("Não consegui baixar a foto. Tente de novo.") from None
        if len(data) > self.PHOTO_MAX_BYTES:
            raise ValueError("A foto passa de 5 MB. Envie uma menor.")
        import cv2
        import numpy as np
        image = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError("Não consegui ler a imagem. Envie como foto (não como arquivo).")
        h, w = image.shape[:2]
        scale = min(1.0, max_side / float(max(h, w)))
        if scale < 1.0:
            image = cv2.resize(image, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 85])
        if not ok:
            raise ValueError("Não consegui preparar a foto.")
        return buf.tobytes()

    def get_bot_username(self, block: bool = True) -> Optional[str]:
        """
        @ do bot (getMe). Telas que só MOSTRAM o nome usam block=False: devolvem o que já se
        sabe e, se ainda não se sabe, buscam em segundo plano — a página nunca espera o Telegram
        (sem internet, cada getMe podia segurar a tela por até 8 s). O nome fica guardado no banco
        por bot (o número antes do ":" do token, que não é segredo).
        """
        if self._bot_username is None and self.enabled:
            self._bot_username = self._cached_name()
        if self._bot_username is not None or not self.enabled:
            return self._bot_username
        if time.time() - self._bot_checked_at <= 60:
            return None
        self._bot_checked_at = time.time()
        if block:
            self._fetch_name()
        else:
            threading.Thread(target=self._fetch_name, daemon=True, name="telegram-getme").start()
        return self._bot_username

    def _bot_key(self) -> str:
        return "telegram_bot_name_" + self.token.split(":", 1)[0][:20]

    def _cached_name(self) -> Optional[str]:
        try:
            from database import system_settings
            return system_settings.get_all().get(self._bot_key()) or None
        except Exception:
            return None

    def _fetch_name(self) -> None:
        ok, result = self._call("getMe")
        if ok and isinstance(result, dict) and result.get("username"):
            self._bot_username = result["username"]
            try:
                from database import system_settings
                system_settings.set_value(self._bot_key(), self._bot_username)
            except Exception:
                pass

    def recent_chats(self, limit: int = 50) -> Tuple[bool, Any]:
        """
        Chats que escreveram para o bot recentemente (getUpdates). Serve para
        descobrir o Chat ID de quem vai receber o aviso: a pessoa manda um
        "oi" para o bot e aparece aqui, sem precisar de @userinfobot.
        """
        ok, result = self._call("getUpdates", {"limit": limit, "timeout": 0})
        if not ok:
            return False, result
        chats: Dict[str, Dict[str, str]] = {}
        for upd in result or []:
            msg = upd.get("message") or upd.get("edited_message") or upd.get("my_chat_member") or {}
            chat = msg.get("chat") or {}
            if "id" not in chat:
                continue
            name = " ".join(x for x in [chat.get("first_name"), chat.get("last_name")] if x) \
                or chat.get("title") or chat.get("username") or "(sem nome)"
            chats[str(chat["id"])] = {
                "chat_id": str(chat["id"]),
                "nome": name,
                "username": chat.get("username", ""),
            }
        return True, list(chats.values())

    # ------------------------------------------------------------ fila assíncrona
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._worker, daemon=True, name="telegram-sender")
        self._thread.start()

    def stop(self) -> None:
        if self._thread and self._thread.is_alive():
            self._queue.put(None)
            self._thread.join(timeout=3.0)

    def enqueue(self, chat_id: str, text: str, callback: Optional[ResultCallback] = None, photo: Optional[bytes] = None, parse_mode: Optional[str] = None) -> bool:
        """Agenda o envio sem bloquear. False se a fila estiver cheia."""
        try:
            self._queue.put_nowait((str(chat_id), text, callback, photo, parse_mode))
            return True
        except queue.Full:
            logger.error("Fila do Telegram cheia; mensagem descartada.")
            return False

    def _worker(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                return
            chat_id, text, callback, photo, parse_mode = item
            ok, detail = self.send_photo(chat_id, photo, text, parse_mode) if photo else self.send_message(chat_id, text, parse_mode)
            if not ok and photo:  # sem foto o aviso ainda vale mais que nada
                ok, detail = self.send_message(chat_id, text, parse_mode)
            if not ok:
                logger.warning(f"Telegram: falha ao enviar para {chat_id}: {detail}")
            if callback:
                try:
                    callback(ok, detail)
                except Exception as e:
                    logger.error(f"Erro no callback do Telegram: {e}")
