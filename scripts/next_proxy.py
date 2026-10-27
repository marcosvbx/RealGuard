"""Proxy HTTPS temporário: senha efêmera, um IP cliente, sem configuração global."""
import base64
import hmac
import ipaddress
import secrets
import select
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import quote


class InstallProxy:
    def __init__(self, bind_ip, client_ip):
        self.password = secrets.token_urlsafe(32)
        expected = 'Basic ' + base64.b64encode(('grupo05:' + self.password).encode()).decode()

        class Handler(BaseHTTPRequestHandler):
            timeout = 30

            def log_message(self, *args):
                pass  # Não registrar URLs, cabeçalhos ou credenciais.

            def do_CONNECT(self):
                if self.client_address[0] != client_ip:
                    self.send_error(403)
                    return
                if not hmac.compare_digest(self.headers.get('Proxy-Authorization', ''), expected):
                    self.send_response(407)
                    self.send_header('Proxy-Authenticate', 'Basic realm="RealGuard installation"')
                    self.end_headers()
                    return
                upstream = None
                try:
                    host, port = self.path.rsplit(':', 1)
                    if int(port) != 443:
                        raise ValueError('Somente HTTPS na porta 443')
                    addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
                    for family, kind, proto, _, address in addresses:
                        if not ipaddress.ip_address(address[0]).is_global:
                            continue
                        try:
                            upstream = socket.socket(family, kind, proto)
                            upstream.settimeout(20)
                            upstream.connect(address)
                            break
                        except OSError:
                            upstream.close()
                            upstream = None
                    if upstream is None:
                        raise OSError('Destino HTTPS indisponível')
                    self.send_response(200, 'Connection established')
                    self.end_headers()
                    self.wfile.flush()
                    peers = [self.connection, upstream]
                    while True:
                        ready, _, _ = select.select(peers, [], [], 60)
                        if not ready:
                            break
                        for source in ready:
                            data = source.recv(65536)
                            if not data:
                                return
                            target = upstream if source is self.connection else self.connection
                            target.sendall(data)
                except (OSError, ValueError):
                    self.close_connection = True
                finally:
                    if upstream:
                        upstream.close()

        self.server = ThreadingHTTPServer((bind_ip, 0), Handler)
        self.server.daemon_threads = True
        self.url = 'http://grupo05:{}@{}:{}'.format(quote(self.password), bind_ip, self.server.server_port)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.server.shutdown()
        self.server.server_close()
