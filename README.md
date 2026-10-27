# RealGuard

Sistema de monitoramento com detecção de pessoas, reconhecimento facial, painel web e integração com Telegram.

## Executar a partir do código

Use Python 3.10+ (Python 3.11 recomendado para este ambiente). No Windows:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item config/realguard.env.example config/realguard.env
```

Configure as credenciais apenas em `config/realguard.env` ou nas variáveis de ambiente. Coloque o modelo YOLO em `models/yolov8n.onnx` (ou configure `MODEL_PATH`). Para reconhecimento facial, disponibilize os modelos `buffalo_sc` em `models/insightface/models/buffalo_sc/`, incluindo `det_500m.onnx` e `w600k_mbf.onnx`.

```powershell
.\.venv\Scripts\python.exe main.py --criar-admin
.\.venv\Scripts\python.exe main.py
```

Para AIBOX, consulte `docs/otimizacao-aibox.md` e `requirements-aibox.txt`.

## Arquivos locais

Esta versão de código não inclui credenciais, banco de dados, fotos, biometria, backups, logs, ambientes Python, modelos de IA ou executáveis. Esses itens estão no `.gitignore`. O exemplo de configuração contém campos vazios para os segredos.

Os atalhos Windows que chamam `RealGuard.exe` e o gerador de instalador dependem dos artefatos locais de compilação. Para executar este código, use os comandos Python acima.

Antes de publicar alterações, confira os arquivos adicionados. Não inclua dados reais de alunos ou credenciais em código, exemplos, imagens ou documentação.
