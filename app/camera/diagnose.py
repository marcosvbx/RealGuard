"""
Por que a câmera não conectou? Diagnóstico em português para o teste de câmera.

O OpenCV só diz "não abriu". Aqui o RealGuard pergunta direto para a câmera (protocolo RTSP,
sem decodificar vídeo) e separa os casos que dá para resolver na hora:

  ip       o IP não responde na porta (desligada, cabo, IP ou porta errados, outra rede)
  caminho  respondeu, mas o caminho do stream não existe nessa câmera (marca diferente)
  senha    respondeu, mas recusou usuário/senha
  rtsp     a porta respondeu, mas não fala RTSP
  ok       a câmera aceitou: se o vídeo não abriu, é formato/rede lenta (tente o substream)
"""

import base64
import hashlib
import os
import re
import socket
from typing import Optional, Tuple

TIMEOUT = 4.0

PATH_HINT = ("Caminhos: padrão (Intelbras/Dahua) /cam/realmonitor?channel=1&subtype=1 · "
             "Haiz /avstream/channel=1/stream=0.sdp · outro modelo: veja o manual da câmera")


def _request(host: str, port: int, url: str, cseq: int, auth: str = "", timeout: float = TIMEOUT) -> Tuple[int, str]:
    lines = [f"DESCRIBE {url} RTSP/1.0", f"CSeq: {cseq}", "Accept: application/sdp", "User-Agent: RealGuard"]
    if auth:
        lines.append(f"Authorization: {auth}")
    with socket.create_connection((host, int(port)), timeout=timeout) as s:
        s.settimeout(timeout)
        s.sendall(("\r\n".join(lines) + "\r\n\r\n").encode())
        data = b""
        while b"\r\n\r\n" not in data and len(data) < 65536:
            chunk = s.recv(4096)
            if not chunk:
                break
            data += chunk
    head = data.split(b"\r\n\r\n", 1)[0].decode("latin-1")
    m = re.match(r"RTSP/\d\.\d (\d{3})", head)
    if not m:
        return 0, head
    return int(m.group(1)), head


def _digest(head: str, user: str, password: str, url: str) -> str:
    m = re.search(r'WWW-Authenticate:\s*Digest\s+(.*)', head, re.I)
    if m:
        params = dict(re.findall(r'(\w+)="?([^",]*)"?', m.group(1)))
        realm, nonce = params.get("realm", ""), params.get("nonce", "")
        ha1 = hashlib.md5(f"{user}:{realm}:{password}".encode()).hexdigest()
        ha2 = hashlib.md5(f"DESCRIBE:{url}".encode()).hexdigest()
        resp = hashlib.md5(f"{ha1}:{nonce}:{ha2}".encode()).hexdigest()
        return (f'Digest username="{user}", realm="{realm}", nonce="{nonce}", uri="{url}", '
                f'response="{resp}"')
    return "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()


def rtsp(host: str, port: int, path: str, user: str = "", password: str = "",
         timeout: float = TIMEOUT) -> Tuple[str, str]:
    """(código, mensagem). Nunca levanta exceção."""
    url = f"rtsp://{host}:{port}{path}"
    try:
        code, head = _request(host, port, url, 1, timeout=timeout)
    except socket.timeout:
        return "ip", (f"O IP {host} não respondeu na porta {port} em {int(timeout)} s. Confira se a câmera está "
                      "ligada, o cabo e se o IP é da mesma rede deste equipamento (192.168.50.x).")
    except ConnectionRefusedError:
        return "ip", f"{host} respondeu, mas a porta {port} está fechada. A porta RTSP costuma ser 554."
    except OSError as e:
        return "ip", f"Não consegui chegar em {host}:{port} ({e.strerror or e}). Confira o IP e a rede."
    if code == 0:
        return "rtsp", f"{host}:{port} respondeu, mas não é RTSP. Confira a porta (normalmente 554)."
    if code == 401:
        if not user:
            return "senha", "A câmera pede usuário e senha. Preencha os dois."
        try:
            code, _ = _request(host, port, url, 2, auth=_digest(head, user, password, url), timeout=timeout)
        except OSError as e:
            return "ip", f"A câmera parou de responder durante o login ({e})."
        if code == 401:
            return "senha", "A câmera recusou o usuário ou a senha (maiúsculas contam; digite a senha sem aspas)."
    if code == 404:
        return "caminho", f"A câmera respondeu, mas o caminho {path} não existe nela. {PATH_HINT}"
    if code in (200, 301, 302):
        return "ok", ("A câmera aceitou usuário, senha e caminho, mas o vídeo não abriu a tempo. Tente o substream "
                      "(subtype=1) ou teste de novo; rede Wi-Fi lenta também causa isso.")
    return "rtsp", f"A câmera respondeu com o código RTSP {code}. {PATH_HINT}"


def webcam(index: int) -> Optional[str]:
    """Mensagem quando a webcam claramente não existe neste equipamento (Linux)."""
    if os.name == "nt" or not os.path.isdir("/dev"):
        return None
    if os.path.exists(f"/dev/video{int(index)}"):
        return None
    if not any(n.startswith("video") for n in os.listdir("/dev")):
        return ("Este equipamento não tem nenhuma webcam. A AIBOX não tem câmera própria, e a webcam do notebook "
                "não chega até ela: use uma câmera IP (RTSP) ou uma webcam USB ligada na AIBOX.")
    return f"Não existe webcam no índice {index} neste equipamento (tente 0, 1 ou 2)."
