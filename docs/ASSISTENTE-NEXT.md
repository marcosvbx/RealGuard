# Assistente de instalação NEXT

Abra `Assistente NEXT.bat` no notebook Windows. O código do assistente está
preparado, mas a instalação e a operação remota ainda precisam ser testadas.
Não foram executados testes nem alterações na IA BOX durante sua criação.

## Preparação

- Notebook: Python 3.9 ou superior e internet. O atalho cria `.venv-assistente`
  separada da venv do RealGuard e instala Paramiko no primeiro uso.
- BOX: Linux ARM64, Python 3.8 com suporte a venv/pip, SSH e SFTP habilitados.
  Este perfil usa `requirements-aibox.txt`. Outras versões são recusadas para
  evitar instalar dependências incompatíveis; exigem preparação específica.
- Notebook e BOX devem se alcançar pela rede. A BOX pode ser `.10`, `.20` ou
  outro endereço. Não precisa estar conectada diretamente à internet.
- Leve os modelos em `models/`. O modelo YOLO deve existir como
  `models/yolov8n.onnx`. O pacote InsightFace pode ser baixado durante a instalação.

## Primeira instalação

1. Abra o atalho, selecione a BOX e informe o usuário (padrão `grupo05`).
2. Digite a senha SSH, ou deixe vazia para usar uma chave já configurada.
   Confira a impressão digital do equipamento na primeira conexão. Chaves
   alteradas não são aceitas automaticamente.
3. Confirme a pasta remota, por padrão `~/eduvision/RealGuard`, e o Python.
4. Escolha **1 — Instalar / atualizar**. A cópia preserva banco, logs, `.env`,
   certificados e venv remotos. Arquivos de código e modelos são atualizados.
5. Confirme o IP do notebook acessível pela BOX. O assistente inicia um proxy
   HTTPS temporário, com senha aleatória e acesso restrito ao IP da BOX.
   Se o firewall do Windows perguntar, permita o Python na rede da BOX.
6. Aguarde criação da venv, pip, verificação dos imports e preparação dos modelos.
   O proxy só é aplicado a esses subprocessos, não ao Linux globalmente, e é
   encerrado ao final. O acesso não é disponibilizado a outros usuários por
   configuração global; as credenciais efêmeras são usadas pela sessão SSH escolhida.
7. Escolha **3 — Configurar câmera IP** e forneça o caminho RTSP correto.
8. Em instalação nova, escolha **4 — Criar primeiro administrador**. Nenhum
   administrador existente é substituído. A base nova começa sem alunos;
   importar a base do notebook não faz parte desta primeira versão.
9. Escolha **5 — Iniciar**, selecione Arena/Completo e a porta (padrão 18005).
   O navegador abre quando `/login` responder. Confira câmera e IA no dashboard.
   Na Arena, cadastre a turma em Escola e associe-a à câmera em Ao vivo.

## Uso diário e encerramento

Não reinstale a cada execução. Conecte-se e use a opção 5. Para encerrar, use
a opção 6. Ela envia Ctrl+C/SIGINT apenas ao processo iniciado pelo assistente,
conferindo PID, identidade e pasta. Não encerra processos de outros projetos.
Sair do assistente não encerra o servidor remoto.

O proxy é somente de instalação. Integrações que precisem de internet durante
o uso do RealGuard continuam dependendo da configuração de rede/proxy de execução.
Esta versão abre HTTP na rede local; instalações existentes com HTTPS devem
ser acessadas pelo endereço HTTPS configurado caso a abertura automática não funcione.

## Falhas e retomada

Use a opção 2 para informações do equipamento e a 7 para as últimas linhas de
`logs/next-console.log`. O notebook mantém um log em `logs/next-assistant/`, com
as senhas digitadas e a credencial do proxy ocultadas.

Se uma dependência do sistema faltar (`python3.8-venv`, compilador, bibliotecas
nativas etc.), prepare esse pré-requisito na BOX e repita a instalação. O
assistente não executa sudo nem troca o Python do sistema. Não grava senhas.

Não execute duas instalações simultâneas. Se a conexão cair ou o assistente for
interrompido, confira se o pip remoto terminou antes de repetir. Arquivos são
transferidos individualmente com substituição após o upload; o conjunto da
atualização não é uma transação. Uma transferência interrompida deve ser repetida
antes de iniciar o RealGuard. A atualização é recusada quando há `main.py` em
execução na pasta escolhida.

Os testes de SSH, proxy/firewall, instalação ARM64, configuração da câmera,
abertura do dashboard e encerramento no hardware ficaram pendentes a pedido
do usuário. Não considerar este assistente validado para o evento sem esse ensaio.
