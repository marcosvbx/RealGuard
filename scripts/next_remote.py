"""Operações remotas do assistente NEXT. Python padrão, compatível com 3.8/Linux.

Recebe JSON por stdin via SSH. Senhas não aparecem na linha de comando.
"""
import json
import os
import platform
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path.cwd().resolve()
STATE = ROOT / 'logs' / 'next-process.json'
PYTHON = ROOT / '.venv' / 'bin' / 'python'


def identity(pid):
    try:
        proc = Path('/proc') / str(int(pid))
        # starttime é posterior ao nome do processo, que pode conter espaços.
        return {'start': (proc / 'stat').read_text().rsplit(')', 1)[1].split()[19],
                'cwd': str((proc / 'cwd').resolve()),
                'cmd': (proc / 'cmdline').read_bytes().replace(b'\0', b' ').decode(errors='replace')}
    except (OSError, ValueError, IndexError):
        return None


def managed():
    if not STATE.exists():
        return None
    state = json.loads(STATE.read_text())
    found = identity(state['pid'])
    if found and found == state['identity']:
        return state
    return None


def ensure_stopped():
    # Inclui instâncias iniciadas fora do assistente, para não trocar código em uso.
    for entry in Path('/proc').iterdir():
        if entry.name.isdigit():
            item = identity(entry.name)
            if item and item['cwd'] == str(ROOT) and 'main.py' in item['cmd']:
                raise RuntimeError('RealGuard está rodando nesta pasta. Encerre antes de instalar/configurar.')


def run(args, env=None):
    code = subprocess.call([str(x) for x in args], env=env)
    if code:
        raise RuntimeError('Etapa falhou (código {}). Consulte a saída acima.'.format(code))


def prepare(payload):
    ensure_stopped()
    if platform.system() != 'Linux' or platform.machine().lower() not in ('aarch64', 'arm64'):
        raise RuntimeError('Este perfil de instalação é Linux ARM64. Não instalar requirements-aibox em outro hardware.')
    if sys.version_info[:2] != (3, 8):
        raise RuntimeError('requirements-aibox prevê Python 3.8. Selecione esse interpretador ou revise as dependências antes de prosseguir.')
    env = os.environ.copy()
    for key in ('HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy'):
        env[key] = payload['proxy']
    env['NO_PROXY'] = env['no_proxy'] = 'localhost,127.0.0.1'
    # Ignora configurações pip globais que possam enviar tráfego fora do proxy escolhido.
    env['PIP_CONFIG_FILE'] = os.devnull
    env['PIP_INDEX_URL'] = 'https://pypi.org/simple'
    env.pop('PIP_EXTRA_INDEX_URL', None)
    env.pop('PIP_TRUSTED_HOST', None)
    print('Testando internet da BOX pelo proxy...', flush=True)
    run([sys.executable, '-c', "import urllib.request; r=urllib.request.urlopen('https://pypi.org/simple/pip/',timeout=25); print('Proxy OK:',r.status)"], env)
    if not PYTHON.exists():
        print('Criando ambiente Linux...', flush=True)
        run([sys.executable, '-m', 'venv', str(ROOT / '.venv')])
    run([PYTHON, '-c', "import sys; assert sys.version_info[:2]==(3,8), 'venv incompatível'"])
    print('Instalando dependências ARM64. Pode levar vários minutos.', flush=True)
    run([PYTHON, '-m', 'pip', 'install', '--disable-pip-version-check', '-r', 'requirements-aibox.txt'], env)
    run([PYTHON, '-m', 'pip', 'check'], env)
    run([PYTHON, '-c', 'import cv2,numpy,onnxruntime,insightface,fastapi,uvicorn,multipart; print("Imports OK")'], env)
    config = ROOT / 'config' / 'realguard.env'
    if not config.exists():
        shutil.copyfile(ROOT / 'config' / 'realguard.env.example', config)
        config.chmod(0o600)
        run([PYTHON, '-c', "from config.setup_wizard import update_env_file; update_env_file('config/realguard.env',{'API_HOST':'0.0.0.0','API_PORT':'18005','DADOS_FICTICIOS':'off','MODEL_PATH':'models/yolov8n.onnx'})"])
    print('Verificando modelos (downloads necessários usam o proxy)...', flush=True)
    run([PYTHON, '-c', "from config.settings import Settings,resolve_insightface_root; from insightface.app import FaceAnalysis; s=Settings(); print('Carregando pacote facial:',s.face_model); FaceAnalysis(name=s.face_model,root=resolve_insightface_root(s.face_model),providers=['CPUExecutionProvider'])"], env)
    if not (ROOT / 'models' / 'yolov8n.onnx').exists():
        raise RuntimeError('models/yolov8n.onnx ausente. Inclua o modelo na pasta do notebook e transfira novamente.')
    print('Preparação concluída. Configure câmera e primeiro administrador.', flush=True)


def main(payload):
    action = payload['action']
    if action == 'inspect':
        print(json.dumps({'sistema': platform.system(), 'arquitetura': platform.machine(),
                          'python': platform.python_version(), 'pasta': str(ROOT),
                          'livre_gb': round(shutil.disk_usage(ROOT).free / 2**30, 1),
                          'venv': PYTHON.exists(), 'processo': managed()}, ensure_ascii=False))
    elif action == 'stopped':
        ensure_stopped()
    elif action == 'prepare':
        prepare(payload)
    elif action == 'configure':
        ensure_stopped()
        sys.path.insert(0, str(ROOT))
        from config.setup_wizard import update_env_file
        allowed = {'CAMERA_MODE','RTSP_URL','CAMERA_IP','CAMERA_PORT','CAMERA_USER','CAMERA_PASS','CAMERA_RTSP_PATH','CAMERA_ID','CAMERA_NAME'}
        values = payload['values']
        if not set(values) <= allowed or any('\n' in str(v) or '\r' in str(v) for v in values.values()):
            raise ValueError('Configuração inválida')
        update_env_file(str(ROOT / 'config' / 'realguard.env'), values)
        print('Câmera configurada. Teste a imagem no dashboard.')
    elif action == 'admin':
        sys.path.insert(0, str(ROOT))
        from database.migrations import initialize_database
        from database import access
        initialize_database()
        if access.count_users(active_admins_only=True):
            raise RuntimeError('Já existe administrador. Use o login existente; nenhuma conta foi alterada.')
        access.create_user(payload['username'], payload['name'], 'admin', payload['password'], False)
        print('Primeiro administrador criado.')
    elif action == 'start':
        ensure_stopped()
        port = int(payload.get('port', 18005))
        if not 1024 <= port <= 65535:
            raise ValueError('Porta inválida')
        with socket.socket() as probe:
            probe.bind(('0.0.0.0', port))
        mode = payload['mode']
        if mode not in ('arena', 'complete'):
            raise ValueError('Modo inválido')
        STATE.parent.mkdir(exist_ok=True)
        with open(ROOT / 'logs' / 'next-console.log', 'ab', buffering=0) as log:
            process = subprocess.Popen([str(PYTHON), 'main.py', '--'+mode, '--no-setup', '--no-browser', '--host', '0.0.0.0', '--port', str(port)], stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
        time.sleep(0.5)
        who = identity(process.pid)
        if process.poll() is not None or who is None:
            raise RuntimeError('Servidor encerrou ao iniciar. Veja logs/next-console.log.')
        STATE.write_text(json.dumps({'pid': process.pid, 'identity': who, 'port': port, 'mode': mode}))
        print('Servidor iniciado; aguardando disponibilidade pelo notebook.')
    elif action == 'stop':
        state = managed()
        if not state:
            print('Nenhum processo iniciado por este assistente está ativo. Outras instâncias não foram encerradas.')
            return
        os.kill(state['pid'], signal.SIGINT)
        for _ in range(40):
            if not managed():
                STATE.unlink(missing_ok=True)
                print('RealGuard encerrado.')
                return
            time.sleep(0.5)
        raise RuntimeError('Encerramento ainda pendente. Consulte os logs; não foi usado encerramento forçado.')
    else:
        raise ValueError('Ação desconhecida')


if __name__ == '__main__':
    try:
        main(json.load(sys.stdin))
    except Exception as exc:
        print('ERRO: {}'.format(exc), file=sys.stderr)
        sys.exit(1)
