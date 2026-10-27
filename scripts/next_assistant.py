"""Assistente NEXT no notebook. Execute pelo atalho Assistente NEXT.bat.

Não instala nada até o usuário escolher a opção de instalação.
"""
import base64
import getpass
import hashlib
import json
import os
from pathlib import Path
import posixpath
import shlex
import socket
import stat
import sys
import time
import urllib.error
import urllib.request
import webbrowser

import paramiko
from next_proxy import InstallProxy

ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / 'logs' / 'next-assistant'
LOCAL.mkdir(parents=True, exist_ok=True)
KNOWN = LOCAL / 'known_hosts'
LOG = LOCAL / time.strftime('installation-%Y%m%d-%H%M%S.log')
SECRETS = []


def say(message):
    message = str(message)
    for secret in SECRETS:
        if secret:
            message = message.replace(secret, '[oculto]')
    print(message, flush=True)
    with LOG.open('a', encoding='utf-8') as stream:
        stream.write(message+'\n')


def ask(label, default=''):
    return input('  '+label+(' ['+default+']' if default else '')+': ').strip() or default


def password(label):
    value = getpass.getpass('  '+label+': ')
    SECRETS.append(value)
    return value


def banner(title):
    print('\n'+'═'*68+'\n  REALGUARD · NEXT — '+title+'\n'+'═'*68)


class VerifyHost(paramiko.MissingHostKeyPolicy):
    def missing_host_key(self, client, hostname, key):
        fingerprint = base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip('=')
        say('Primeira conexão com {}. Chave {} SHA256:{}'.format(hostname,key.get_name(),fingerprint))
        if ask('Confirme a identidade da BOX. Confiar nesta chave? (sim/não)', 'não').lower() != 'sim':
            raise RuntimeError('Conexão cancelada; chave não aceita.')
        client.get_host_keys().add(hostname,key.get_name(),key)
        client.save_host_keys(str(KNOWN))


class Assistant:
    def __init__(self):
        self.client = None
        self.sftp = None

    def connect(self):
        if self.client:
            self.client.close()
        banner('ESCOLHER EQUIPAMENTO')
        for host in ('192.168.50.10','192.168.50.20'):
            try:
                with socket.create_connection((host,22),timeout=2):
                    say(host+' — SSH acessível')
            except OSError:
                say(host+' — sem resposta SSH')
        choice=ask('1: .10 | 2: .20 | 3: outro IP', '1')
        if choice not in ('1','2','3'):
            raise ValueError('Escolha 1, 2 ou 3.')
        self.host={'1':'192.168.50.10','2':'192.168.50.20'}.get(choice) or ask('IP da BOX')
        self.host=socket.gethostbyname(self.host)
        self.user=ask('Usuário SSH','grupo05')
        self.client=paramiko.SSHClient()
        if KNOWN.exists():
            self.client.load_host_keys(str(KNOWN))
        self.client.set_missing_host_key_policy(VerifyHost())
        auth=password('Senha SSH (vazia para tentar chave SSH)')
        self.client.connect(self.host,username=self.user,password=auth or None,timeout=10,banner_timeout=15,auth_timeout=20,allow_agent=True,look_for_keys=not bool(auth))
        self.client.get_transport().set_keepalive(20)
        self.sftp=self.client.open_sftp()
        home=self.sftp.normalize('.')
        self.remote=posixpath.normpath(ask('Pasta do projeto na BOX',home+'/eduvision/RealGuard'))
        if not self.remote.startswith(home+'/') or self.remote==home:
            raise ValueError('Use uma pasta específica dentro de '+home)
        self.python=ask('Python Linux para preparar a venv','python3')
        if not self.python or any(c in self.python for c in '\r\n'):
            raise ValueError('Interpretador inválido')
        self.command('mkdir -p '+shlex.quote(self.remote))
        if self.sftp.normalize(self.remote)!=self.remote:
            raise ValueError('A pasta escolhida contém um link simbólico. Use o caminho real.')
        self.helper=home+'/.realguard-next-helper.py'
        self.sftp.put(str(ROOT/'scripts'/'next_remote.py'),self.helper)
        self.sftp.chmod(self.helper,0o600)
        say('Conectado a {} como {}. Pasta: {}'.format(self.host,self.user,self.remote))
        self.action('inspect')

    def command(self, command, payload=None):
        stdin,stdout,stderr=self.client.exec_command(command)
        channel=stdout.channel
        channel.set_combine_stderr(True)
        if payload is not None:
            stdin.write(json.dumps(payload,ensure_ascii=False)+'\n')
            stdin.flush()
        channel.shutdown_write()
        pending=''
        try:
            while True:
                if channel.recv_ready():
                    pending+=channel.recv(65536).decode('utf-8',errors='replace')
                    while '\n' in pending:
                        line,pending=pending.split('\n',1)
                        say(line.rstrip('\r'))
                elif channel.exit_status_ready():
                    break
                elif channel.closed:
                    raise RuntimeError('Conexão SSH interrompida. Verifique o destino antes de repetir a operação.')
                else:
                    time.sleep(0.1)
            if pending:
                say(pending)
            status=channel.recv_exit_status()
            if status:
                raise RuntimeError('Etapa remota falhou (código {}).'.format(status))
        finally:
            channel.close()

    def action(self,name,venv=False,**values):
        interpreter=self.remote+'/.venv/bin/python' if venv else self.python
        self.command('cd {} && {} {}'.format(shlex.quote(self.remote),shlex.quote(interpreter),shlex.quote(self.helper)),dict(action=name,**values))

    def transfer(self):
        self.action('stopped')
        say('Transferindo código/modelos. Banco, logs, certificados, .env e venv da BOX serão preservados.')
        roots=['app','api','database','config','web','models','scripts']
        files=[ROOT/name for name in ('main.py','requirements-aibox.txt','requirements.txt','iniciar.sh')]
        for name in roots:
            files.extend(p for p in (ROOT/name).rglob('*') if p.is_file())
        count=0
        parents=set()
        for source in files:
            rel=source.relative_to(ROOT)
            if source.is_symlink() or any(part in ('__pycache__','certs','.git','.pytest_cache') for part in rel.parts):
                continue
            if source.suffix in ('.pyc','.pyo') or rel.as_posix()=='config/realguard.env':
                continue
            target=self.remote+'/'+rel.as_posix()
            parent=posixpath.dirname(target)
            if parent not in parents:
                self.command('mkdir -p '+shlex.quote(parent))
                if self.sftp.normalize(parent)!=parent:
                    raise RuntimeError('Transferência recusada: diretório com link simbólico '+parent)
                parents.add(parent)
            try:
                attr=self.sftp.lstat(target)
                if stat.S_ISLNK(attr.st_mode):
                    raise RuntimeError('Destino é link simbólico: '+target)
                if attr.st_size==source.stat().st_size and int(attr.st_mtime)==int(source.stat().st_mtime):
                    continue
            except FileNotFoundError:
                pass
            temp=target+'.next-upload'
            try:
                self.sftp.lstat(temp)
                self.sftp.remove(temp)
            except FileNotFoundError:
                pass
            self.sftp.put(str(source),temp)
            self.sftp.utime(temp,(source.stat().st_atime,source.stat().st_mtime))
            self.sftp.posix_rename(temp,target)
            count+=1
            if count%25==0:
                say('{} arquivos enviados...'.format(count))
        say('Transferência concluída: {} arquivos atualizados.'.format(count))

    def install(self):
        banner('INSTALAÇÃO / ATUALIZAÇÃO')
        say('Destino: {} | {} | {}'.format(self.host,self.user,self.remote))
        say('O banco existente será mantido. Uma instalação nova começará sem alunos.')
        if ask('Transferir projeto e instalar dependências? (sim/não)','não').lower()!='sim':
            return
        self.transfer()
        # Escolhe a interface pela rota para a BOX, não pela conexão com a internet.
        with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as probe:
            probe.connect((self.host,22))
            bind_ip=probe.getsockname()[0]
        bind_ip=ask('IP do notebook acessível pela BOX',bind_ip)
        with InstallProxy(bind_ip,self.host) as proxy:
            SECRETS.extend([proxy.url,proxy.password])
            say('Proxy temporário em {}:{}; somente a BOX selecionada com autenticação.'.format(bind_ip,proxy.server.server_port))
            say('Se o Windows solicitar acesso à rede, permita na rede usada pela BOX. O assistente não altera o firewall.')
            try:
                self.action('prepare',proxy=proxy.url)
            finally:
                say('Encerrando proxy temporário de instalação.')
        say('Próximos passos: configurar câmera, criar primeiro administrador e iniciar.')

    def configure(self):
        banner('CÂMERA IP')
        values={'CAMERA_MODE':'rtsp','RTSP_URL':'','CAMERA_ID':'1',
                'CAMERA_NAME':ask('Nome da câmera','Sala NEXT'),
                'CAMERA_IP':ask('IP da câmera','192.168.50.108'),
                'CAMERA_PORT':ask('Porta RTSP','554'),
                'CAMERA_USER':ask('Usuário da câmera','admin'),
                'CAMERA_PASS':password('Senha da câmera'),
                'CAMERA_RTSP_PATH':ask('Caminho RTSP (confira o modelo)','/cam/realmonitor?channel=1&subtype=1')}
        self.action('configure',venv=True,values=values)
        say('Em Ao vivo, teste a câmera. Na Arena, use Definir turma após cadastrar em Escola → Turmas.')

    def admin(self):
        username=ask('Login do primeiro administrador','admin')
        name=ask('Nome do administrador')
        secret=password('Senha nova do dashboard')
        if secret!=password('Repita a senha'):
            raise ValueError('Senhas diferentes')
        self.action('admin',venv=True,username=username,name=name,password=secret)

    def start(self):
        mode=ask('1: Arena | 2: Completo','1')
        if mode not in ('1','2'):
            raise ValueError('Escolha 1 ou 2')
        port=int(ask('Porta do dashboard','18005'))
        self.action('start',mode='arena' if mode=='1' else 'complete',port=port)
        url='http://{}:{}'.format(self.host,port)
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        say('Aguardando servidor por até 120 segundos...')
        deadline=time.monotonic()+120
        while time.monotonic()<deadline:
            try:
                with opener.open(url+'/login',timeout=3) as response:
                    if response.status==200:
                        say('Dashboard disponível: '+url)
                        webbrowser.open(url)
                        say('Servidor respondeu. Confira câmera e IA em Ao vivo; a resposta HTTP não comprova reconhecimento.')
                        return
            except (OSError,urllib.error.URLError):
                pass
            time.sleep(2)
        raise RuntimeError('Dashboard ainda indisponível. Consulte os logs (opção 7). O processo pode continuar iniciando.')

    def menu(self):
        self.connect()
        while True:
            banner(self.host+' · '+self.user)
            print('  1  Instalar / atualizar projeto e dependências\n  2  Verificar equipamento\n  3  Configurar câmera IP\n  4  Criar primeiro administrador\n  5  Iniciar e abrir dashboard\n  6  Encerrar instância iniciada pelo assistente\n  7  Mostrar últimas linhas do log remoto\n  8  Selecionar outra BOX\n  0  Sair (servidor continua rodando)\n')
            choice=ask('Opção','0')
            try:
                if choice=='0':
                    return
                elif choice=='1': self.install()
                elif choice=='2': self.action('inspect')
                elif choice=='3': self.configure()
                elif choice=='4': self.admin()
                elif choice=='5': self.start()
                elif choice=='6': self.action('stop')
                elif choice=='7': self.command('tail -n 70 '+shlex.quote(self.remote+'/logs/next-console.log'))
                elif choice=='8': self.connect()
                else: say('Escolha uma opção do menu.')
            except Exception as exc:
                say('Não concluído: '+str(exc))
                say('Confira a mensagem e tente novamente. Logs locais: '+str(LOG))


if __name__=='__main__':
    assistant=Assistant()
    try:
        assistant.menu()
    except KeyboardInterrupt:
        say('Assistente interrompido. Uma etapa remota em andamento pode continuar; verifique antes de repetir.')
    except Exception as exc:
        say('Não foi possível continuar: '+str(exc))
        sys.exit(1)
    finally:
        if assistant.client:
            assistant.client.close()
