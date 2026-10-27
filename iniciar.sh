#!/bin/sh
# Inicia o RealGuard na AIBOX (ou em qualquer Linux): ativa o Python do grupo e sobe tudo.
#   sh iniciar.sh                 (em primeiro plano: painel de status no terminal)
#   sh iniciar.sh --segundo-plano (continua rodando depois de fechar o SSH; log em logs/console.log)
cd "$(dirname "$0")" || exit 1
if [ -f .venv/bin/activate ]; then
    . .venv/bin/activate
elif [ -f /opt/eduvision/venv/bin/activate ]; then
    . /opt/eduvision/venv/bin/activate
fi
if [ "$1" = "--segundo-plano" ]; then
    shift
    mkdir -p logs
    pkill -u "$(id -u)" -f "python main.py" 2>/dev/null
    nohup python main.py --no-setup "$@" > logs/console.log 2>&1 &
    sleep 1
    echo "RealGuard rodando em segundo plano. Painel de status: cat logs/console.log"
    echo "Parar: pkill -u \$USER -f 'python main.py'"
    exit 0
fi
exec python main.py "$@"
