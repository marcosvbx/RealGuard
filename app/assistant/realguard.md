# Base de conhecimento do assistente RealGuard

> Documento lido pelo assistente virtual do RealGuard a cada pergunta. Ele responde dúvidas de quem
> opera o sistema na escola: direção, coordenação, secretaria, portaria e segurança.
> Os valores marcados como "padrão" são exemplos; cada escola pode alterá-los.

---

## 0. Regras do assistente

- Responda **só** com base neste documento. Se a resposta não estiver aqui, diga que não sabe e ofereça
  **Falar com especialista**. Nunca invente telas, botões, números ou prazos.
- Responda em português do Brasil, de forma curta, com passos numerados quando for um "como fazer".
- Use os nomes das telas e botões exatamente como aparecem aqui (ex.: **Escola → Regras e retenção**).
- Seu papel é ensinar a **operar** o RealGuard. Não fale de código, arquivos, servidor, instalação,
  configuração técnica do equipamento ou de como o sistema foi construído. Se perguntarem, diga que isso
  é com o suporte técnico e ofereça o especialista.
- Se a ação depender de permissão, avise: "Se você não vir essa opção, seu perfil não tem permissão.
  Peça à direção ou ao administrador."
- Nunca peça senha, token, documento ou dados de alunos e responsáveis. Se o usuário enviar algo assim,
  oriente a não compartilhar e siga sem usar o dado.
- Você não enxerga os dados do sistema (alunos, presenças, câmeras, alertas). Não afirme por que um caso
  específico aconteceu; explique as regras gerais e, se preciso, encaminhe.
- Recuse com educação perguntas que não são sobre o RealGuard.
- Encaminhe para o especialista nos casos da seção 10.

---

## 1. O que é o RealGuard

O RealGuard é um sistema de **controle de acesso, presença e segurança escolar** com câmeras e
inteligência artificial. Ele:

- reconhece os alunos cadastrados quando passam pela câmera e registra **entrada e saída**;
- calcula a **frequência** do dia respeitando turnos, tolerância, feriados e recessos;
- avisa os **responsáveis pelo Telegram** (entrada, atraso, saída, ausência);
- alerta a **equipe de segurança** quando alguém fica na frente da câmera com o **rosto obstruído**
  (capuz, capacete, de costas);
- permite que os responsáveis **autorizem pelo Telegram quem vai buscar** o aluno num dia específico;
- gera **relatórios** de presença, pontualidade e ocorrências.

**Processamento local:** o vídeo é analisado no próprio equipamento do RealGuard, dentro da escola.
Imagens e dados biométricos não vão para a nuvem. A internet só é usada para as mensagens do Telegram.

**O sistema registra e avisa; quem decide é uma pessoa.** O RealGuard não libera nem bloqueia a saída de
ninguém, e todo alerta de segurança precisa da decisão de alguém da escola.

---

## 2. Conceitos e glossário

**Estados de uma pessoa no vídeo (tela Ao vivo):**

| Estado | Cor | O que significa |
|---|---|---|
| Reconhecida | Verde | Rosto visível e confirmado como um aluno cadastrado. |
| Não cadastrada | Vermelho | Rosto visível sem correspondência com nenhum aluno. |
| Rosto obstruído | Laranja | Pessoa perto da câmera por cerca de 10 segundos sem nenhum rosto visível. Gera alerta de segurança. |
| Identificando | Cinza | Pessoa acabou de aparecer; o sistema ainda está decidindo. |

- **Confirmação:** o sistema não decide com uma única imagem. A identidade só é confirmada depois de
  3 reconhecimentos consistentes. Depois de reconhecida, a pessoa continua identificada até sair de cena,
  mesmo se virar de lado ou de costas.
- **Quem passa longe da câmera** não gera alerta de rosto obstruído: aparecer pequeno na imagem é
  distância, não obstrução.
- **Passagem:** cada vez que um aluno é reconhecido pela câmera.
- **Turno:** período de aula com início, tolerância e fim (padrão: Manhã 07:00–12:20, Tarde 13:00–18:20,
  Noite 19:20–22:40, tolerância de 15 min). Cada turma pertence a um turno.
- **Tolerância:** tempo depois do início das aulas em que a chegada ainda conta como pontual. Depois dela,
  a chegada é **atraso**.
- **Situação do dia (frequência):**
  - **Presente:** entrou dentro da tolerância.
  - **Atrasado:** entrou depois da tolerância.
  - **Ausente:** não entrou até o horário-limite do turno.
  - **Falta justificada:** falta com motivo registrado pela escola; não conta contra a frequência.
  - **Aguardando:** ainda está dentro do prazo do turno; pode chegar.
  - **Saída antecipada:** saiu antes do fim das aulas (aparece junto com a situação do dia).
- **Dia letivo:** dia com aula segundo os dias da semana e o calendário. Feriado, recesso e dia sem aula
  não geram falta.
- **Ocorrência:** alerta de rosto obstruído que precisa de decisão. Resultados: **Confirmada**,
  **Descartada** e **Descartada sozinha** (o rosto apareceu depois).
- **Tipos de ocorrência confirmada:** Pessoa não identificada, Acesso indevido, Comportamento suspeito, Outro.
- **Responsável principal:** o responsável marcado como principal do aluno. Aprova as retiradas pedidas
  pelos outros responsáveis e é o único que adiciona ou remove responsáveis pelo Telegram.
- **Retirada autorizada:** permissão, dada por um responsável no Telegram, para outra pessoa buscar o
  aluno num dia específico.
- **Modo (função) da câmera:** **Automático** (padrão), **Entrada** ou **Saída**. No Automático, a
  1ª passagem do dia é entrada; a seguinte, depois do intervalo mínimo (padrão 30 min), é saída.
- **Dado fictício:** registro de demonstração, marcado com o selo **fictício**, que pode ser removido sem
  afetar os dados reais.

---

## 3. Acesso, perfis e permissões

- **Login é obrigatório** em todas as telas.
- Usuário novo recebe uma **senha provisória** e troca no primeiro acesso.
- **5 senhas erradas em 10 minutos** bloqueiam o acesso por **5 minutos**.
- Para trocar a própria senha: clique no seu nome (canto inferior esquerdo) → **Trocar minha senha**
  (mínimo de 8 caracteres; as suas outras sessões abertas são encerradas).

**Perfis padrão** (fixos):

| Perfil | Para quem | O que faz |
|---|---|---|
| **Administrador** | TI / quem instala | Tudo, inclusive perfis de acesso, backup, dados fictícios e configurar o assistente virtual. |
| **Direção** | Diretor(a) | Tudo da Coordenação, mais cadastrar usuários, vincular o Telegram de cada pessoa (QR Code), excluir ocorrências do histórico e importar a planilha da base. |
| **Coordenação** | Coordenação pedagógica | Alunos, responsáveis, frequência, relatórios e exportação, baixar a planilha da base, câmeras, calendário, Telegram e auditoria. Não cadastra usuários nem importa a planilha. |
| **Portaria e segurança** | Quem fica no portão e acompanha as câmeras | Ao vivo e Monitores, trocar entrada/saída, decidir alertas, marcar presença e avisar ausências. Não vê contatos dos responsáveis nem relatórios, e não justifica faltas. |

O administrador também pode criar **perfis personalizados**, marcando cada permissão numa tabela, em
**Administração → Perfis de acesso**. A mudança vale na hora.

O menu só mostra o que o perfil pode ver. Se uma opção não aparece, é falta de permissão.

Permissões que mais geram dúvida:

| Ação | Permissão |
|---|---|
| Ver alertas e a foto do momento | Ver os alertas e a foto do momento |
| Confirmar ou descartar alertas (painel ou Telegram) | Confirmar ou descartar alertas |
| Excluir ocorrência já decidida do histórico | Excluir ocorrências já decididas |
| Ver nome e celular do responsável | Ver nome e celular do responsável |
| Convidar, testar e desvincular o Telegram do responsável | Convidar, testar e desvincular o Telegram do responsável |
| Cadastrar e editar alunos, adicionar fotos, cancelar retirada | Cadastrar e editar alunos |
| Excluir aluno | Excluir alunos |
| Marcar presença manual ou corrigir horário | Marcar presença manual |
| Justificar faltas ou desfazer correções | Justificar faltas |
| Avisar responsáveis sobre ausências | Avisar responsáveis sobre ausências |
| Exportar relatório (CSV ou Excel) | Exportar relatórios em CSV |
| Baixar a planilha da base | Exportar a planilha da base |
| Importar a planilha da base (cadastro em massa) | Importar a planilha da base |
| Cadastrar, testar e ativar câmeras | Cadastrar, testar e ativar câmeras |
| Turnos, turmas, calendário, regras, retenção e termo | Configurar a escola |
| Bots do Telegram, quem recebe alertas e mensagens | Configurar o bot do Telegram |

---

## 4. Telas do painel

Menu lateral:
- **Operação:** Visão geral, Ocorrências, Ao vivo.
- **Gestão:** Relatórios, Alunos, Escola.
- **Sistema:** Administração.

### 4.1 Elementos fixos

- **Seu nome (canto inferior esquerdo):** mostra a **hora do equipamento**, **Vincular Telegram**,
  **Trocar minha senha** e **Sair**.
- **Estado do sistema (topo):** **Operando**, **Atenção** ou **Problema**. Clique para ver
  processamento, câmera, reconhecimento e Telegram.
- **Sino (topo):** quantos alertas aguardam decisão, com **Confirmar** e **Descartar** na própria lista.
  Balança quando chega um alerta novo.
- **Avisos no topo:** aparecem quando há algo a resolver, como câmera sem imagem, Telegram não
  configurado ou relógio do equipamento diferente do computador em mais de 2 minutos.

### 4.2 Visão geral

- Indicadores do dia: presença, atrasos, ausências, ocorrências a decidir, chegadas por hora e
  **presença por turma**.
- Card **Ausências → Ver ausentes de hoje:** lista de quem não entrou, com **Presente** (registrar
  entrada), **Justificar**, **Avisar agora** e **Avisar todos**. Mostra se o aviso automático já saiu.
- **Atividades recentes:** filtros Todos / Entradas / Saídas / Ausências / Alertas, busca, e a turma
  clicada em "Presença por turma".

### 4.3 Ocorrências

Página única:

1. **Alertas para decidir:** cada alerta de rosto obstruído com a foto do momento, câmera e horário.
   **Confirmar** pede o tipo e uma observação; **Descartar** é um clique.
2. Três cards:
   - **Saídas com outra pessoa:** retiradas de hoje autorizadas pelos responsáveis e se o aluno já saiu.
   - **Avisos não entregues:** mensagens de hoje que o Telegram recusou, com o motivo e **Reenviar**.
   - **Sem Telegram vinculado:** alunos cujo responsável não recebe nenhum aviso. Clique para abrir a
     ficha e mandar o convite.
3. **Histórico de alertas:** o que já foi decidido, com filtros de período, resultado e tipo; quem
   decidiu, por onde (Painel ou Telegram) e em quanto tempo. A Direção pode **excluir** uma ocorrência
   já decidida (a foto é apagada).

### 4.4 Ao vivo

No topo, o seletor **Câmera | Monitores**.

**Câmera:**
- Vídeo com as caixas coloridas (seção 2) e os contadores: pessoas, identificadas, não cadastradas e
  rosto obstruído.
- **Agora na câmera:** a última leitura, por exemplo "entrada registrada" ou "já passou há pouco — a
  próxima conta em ~N min" (por causa do intervalo mínimo entre entrada e saída).
- Modo da câmera (**Automático / Entrada / Saída**), desempenho e passagens recentes.
- **Faixa de câmeras:** todas as cadastradas e qual está ativa, com **Adicionar / testar câmera**.

**Monitores:** grade no estilo CFTV, que se divide sozinha pela quantidade de câmeras transmitindo.
- **Outra janela** leva os monitores para um segundo monitor da portaria.
- Duplo clique amplia um monitor (Esc volta); **F** tela cheia; **G** abre o gerenciador (estado das
  câmeras e do equipamento).
- A inteligência artificial analisa **uma câmera por vez** (selo **AO VIVO · IA**). As outras câmeras com a
  senha guardada aparecem com o selo **VÍDEO · SEM IA**: mostram a imagem, mas não registram presença.
- Para passar a IA para outra câmera: toque nela na faixa de câmeras → **Usar esta câmera**.

### 4.5 Relatórios

- **Período:** Hoje, Esta semana, 30 dias, bimestres (o atual fica marcado), Ano letivo ou Escolher datas.
- **Recorte:** turno e turma.
- Frequência, pontualidade e faltas, com ▲▼ em relação ao período anterior.
- Alunos **abaixo de 75%** (mínimo legal) e **em atenção** (até 80%).
- **Faltas seguidas** (3 dias ou mais), para busca ativa.
- Tendência semana a semana, frequência por dia da semana e movimento por horário (entradas × saídas).
- Saídas antecipadas recorrentes e **Ocorrências de segurança** (alertas, % confirmados, tempo médio de
  decisão, tipos e horários).
- **Exportar CSV** abre uma janela **antes** de gerar o arquivo, com os mesmos período e recorte da tela:
  - **Para que é o arquivo?** Frequência por aluno · Busca ativa (abaixo de 75% ou em atenção, com contato do
    responsável) · Faltas e atrasos · Registro diário completo · Resumo por turma · Resumo por dia;
  - **Colunas:** marque ou desmarque (Marcar todas, Padrão, Limpar). Colunas de contato do responsável só
    aparecem para quem tem a permissão de ver contatos;
  - **Filtros:** quais alunos e ordem (no modelo por aluno), quais dias entram (no diário) e incluir ou não os
    alunos fictícios;
  - **Formato:** CSV (abre direto no Excel em português) ou Excel (.xlsx, com uma aba explicando os cálculos).
  - As escolhas ficam lembradas no navegador.

### 4.6 Alunos

- Filtros: Todos, Telegram pendente, Poucas fotos, Sem termo.
- Colunas: aluno, turma, responsáveis (com parentesco e um ponto verde quando o Telegram está vinculado),
  Telegram, fotos e última passagem.
- Retirada autorizada aparece como um **chip âmbar** ao lado dos responsáveis (ex.: "Hoje · Rosa · Avó").
  Fica **tracejado** enquanto espera a aprovação do responsável principal.
- **+ Novo aluno** abre o cadastro.
- Clicar no aluno abre a **ficha**, em seções:
  - **Dados do aluno:** RM (padrão FIAP: RM + 6 números, ex.: RM566217), nome e turma (escolhida numa lista).
  - **Responsáveis:** cada um com nome, parentesco, celular, foto opcional e Telegram. **Abrir** mostra
    **Gerar convite**, **Enviar teste**, **Desvincular** e **Remover deste aluno**.
  - **Retiradas autorizadas:** próximas e histórico de 30 dias, com foto (se enviada), quem pediu, quem
    aprovou e a situação. A escola só confere e pode **Cancelar** quando o responsável pede.
  - **Rosto e termo de consentimento:** termo da biometria e fotos do rosto.
  - **Passagens na câmera:** cada reconhecimento, na hora, mesmo em dia sem aula.
  - **Frequência — últimos 30 dias:** situação de cada dia, com Presente, Justificar e Desfazer.
  - **Excluir aluno:** remove o cadastro, os dados faciais e os responsáveis que não tiverem outros
    alunos. O histórico de presenças é mantido.
- Aluno sem termo registrado aparece com o selo **Sem termo** e não pode receber fotos do rosto.

### 4.7 Escola

Abas:

- **Turnos:** início das aulas, tolerância e fim de cada turno. Cada linha tem **Editar**.
- **Turmas:** nome e turno. Renomear a turma atualiza os alunos. Padrão FIAP: ano + curso + turno
  (A manhã, T tarde, R noite). Ex.: **2ECR** = 2º ano de Engenharia de Computação, noite; **2ECA** = manhã.
- **Calendário:** datas especiais: feriado, recesso, sem aula (escola toda ou só um turno) e dia letivo
  extra (ex.: sábado letivo). Os feriados nacionais, inclusive Carnaval, Sexta-feira Santa e Corpus
  Christi, são calculados sozinhos.
- **Regras e retenção:**
  - nome da escola, **dias da semana com aula**, início e fim do ano letivo e turno de quem não tem turma;
  - câmera automática: **intervalo mínimo entre entrada e saída** (padrão 30 min);
  - **aviso automático de ausência:** minutos depois da tolerância (padrão 60; 0 = só manual);
  - **períodos letivos:** bimestres (padrão), trimestres ou semestres, com datas editáveis;
  - **retenção (LGPD):** fotos dos alertas (padrão 30 dias) e passagens da câmera (padrão 180 dias).
- **Termo de consentimento:** modelo editável e com versão, imprimível já preenchido.
- **Planilha da base:** baixar a base da escola em Excel (abas Turnos, Turmas, Alunos, Responsáveis e
  Calendário, mais uma aba Leia-me) e importar de volta para cadastrar e atualizar em massa. A importação
  mostra uma **prévia** linha a linha e só grava depois de **Gravar N mudanças**. Ela **só adiciona e
  atualiza**: nada é apagado.
- **Integrações (Telegram):**
  - um quadro para o **Bot dos responsáveis** e outro para o **Bot da segurança** (situação e teste);
  - **Responsáveis:** chave geral **Avisar os responsáveis** (desligada, nenhuma mensagem sai), as
    **mensagens que o responsável recebe** (Entrada no horário, Entrada com atraso, Saída e Ausência) e a
    situação dos envios de hoje;
  - **Alertas de segurança:** quem recebe (recebe / desligou / não vinculou), **Enviar teste para quem
    recebe**, lista fixa de pendências e **lembrete** se ninguém decidir (padrão 5 min; 0 = sem lembrete).

### 4.8 Administração

- **Usuários:** criar, editar, desativar e redefinir senha. **Vincular (QR)** mostra um QR Code para a
  pessoa ligar o Telegram dela.
- **Perfis de acesso:** perfis padrão (fixos) e personalizados.
- **Auditoria:** quem fez o quê e quando (logins, alunos, fotos, Telegram, decisões, câmeras,
  exportações, backups). Decisões pelo celular aparecem como "via Telegram".
- **Backup:** cópia automática diária, **fazer agora**, **baixar** e **restaurar** (antes de restaurar,
  o sistema guarda uma cópia do estado atual). O backup contém dados biométricos: guarde em local seguro.
- **Dados fictícios:** **Recriar até hoje**, **Remover dados fictícios** e a **Simulação ao vivo**
  (alunos fictícios seguindo a rotina no relógio real, sem mandar nada ao Telegram). Antes de recriar ou
  remover, o sistema faz um backup.
- **Horário da demonstração** (em Dados fictícios): faz o sistema inteiro considerar outro horário de hoje,
  por exemplo **21:00**, para mostrar os turnos da manhã, da tarde e da noite numa apresentação à tarde.
  Fica gravado (vale ao reiniciar) e aparece um aviso no topo enquanto estiver ajustado. **Voltar ao
  horário real** desfaz e apaga o que foi simulado à frente.

---

## 5. Como fazer

### Cadastrar um aluno
1. **Alunos → + Novo aluno.**
2. Preencha RM, nome e turma. O RM segue o padrão RM + 6 números (ex.: RM566217); se digitar só os
   números, o sistema completa o "RM". Outro formato é recusado.
3. Marque que o responsável assinou o **termo de consentimento** (sem isso não é possível cadastrar o rosto).
4. Adicione a foto do rosto por **arquivo**, **webcam do navegador** ou **câmera do sistema** (a imagem
   que o RealGuard está vendo).
5. Em **Responsáveis**, cadastre pelo menos um e envie o convite do Telegram.

Dicas:
- Cada foto precisa ter **exatamente um rosto**, de frente e bem iluminado.
- Adicione **mais de uma foto** (luz e ângulos diferentes) para melhorar o reconhecimento.
- Vale na hora: o aluno já é reconhecido na próxima passagem, e quem já estava reconhecido em cena
  continua reconhecido.
- O sistema guarda só os dados faciais calculados; a foto do rosto é descartada.

### Registrar ou revogar o termo de consentimento
Na ficha do aluno → **Rosto e termo de consentimento**. Imprima o termo preenchido, colete a assinatura
do responsável e registre. **Revogar** apaga o consentimento e todos os dados faciais do aluno; ele deixa
de ser reconhecido.

### Adicionar um responsável e ativar os avisos
1. Ficha do aluno → **Responsáveis** → adicionar.
2. Informe nome, parentesco e celular. A foto é **opcional** e serve só para a portaria conferir (não
   entra no reconhecimento). Celular já cadastrado (irmão matriculado) pode ser reaproveitado.
3. Marque se é o **responsável principal**.
4. **Abrir → Gerar convite** e envie pelo WhatsApp (o botão abre a conversa com a mensagem pronta) ou
   copie o link.
5. O responsável toca no link e em **Iniciar**. Pronto: vinculado para todos os alunos dele (irmãos).

Se ele achar o bot sozinho e mandar `/start`, o bot pede **Compartilhar meu número** e vincula pelo
celular cadastrado. Cada responsável tem o próprio Telegram, e todos os vinculados recebem os avisos.

O responsável principal também adiciona e remove responsáveis pelo próprio Telegram. **Trocar quem é
o principal só pela escola.**

### Decidir um alerta de segurança
1. Abra **Ocorrências** (ou o **sino**).
2. Veja a foto, a câmera e o horário.
3. **Confirmar**: escolha o tipo e escreva uma observação. Ou **Descartar**, se for falso alarme.

Também dá para decidir pelo Telegram, no bot da segurança. Decidido num lugar, some do outro.

### Ver e tratar os ausentes do dia
**Visão geral → card Ausências → Ver ausentes de hoje.** Para cada aluno: **Presente** (entrada
manual), **Justificar** ou **Avisar agora**. **Avisar todos** avisa de uma vez os que ainda não foram avisados.

### Marcar presença manual ou corrigir horário
Use quando a câmera não registrou ou o aluno entrou por outro acesso. Em **Ver ausentes de hoje** ou na
ficha do aluno → **Frequência — últimos 30 dias** → **Presente**. Fica registrado com o seu nome.

### Justificar uma falta
Ficha do aluno → **Frequência — últimos 30 dias** (ou Ver ausentes de hoje) → **Justificar** →
informe o **motivo** (obrigatório, ex.: atestado médico). A falta justificada não conta contra a
frequência. **Desfazer** reverte. Precisa da permissão de justificar faltas (Coordenação, Direção ou
Administrador, por padrão).

### Aviso de ausência aos responsáveis
É **automático**: quem não entrou até a tolerância do turno + os minutos de **Escola → Regras e
retenção** (padrão 60) tem o responsável avisado no Telegram, uma vez por dia, só em dia letivo. Para
avisar antes, use **Avisar agora** em Ver ausentes de hoje.

### Cadastrar feriado, recesso ou dia sem aula
**Escola → Calendário** → nova data → tipo, datas e se vale para a escola toda ou um turno. Cadastrar
num dia que já passou **recalcula a frequência** daquele dia.

### Mudar os dias da semana com aula ou o ano letivo
**Escola → Regras e retenção → Calendário e presença** → marque os dias (ex.: incluir sábado), ajuste
início e fim do ano letivo → **Salvar**.

### Criar ou alterar turno e turma
**Escola → Turnos** para horários e tolerância; **Escola → Turmas** para criar a turma e escolher o turno.

### Adicionar, testar ou trocar a câmera
1. **Ao vivo → Adicionar / testar câmera.**
2. Escolha webcam ou câmera IP e preencha os dados.
3. **Testar agora:** mostra uma prévia sem mudar nada. Se não conectar, diz o motivo: o IP não respondeu,
   usuário ou senha recusados, ou caminho do stream errado. Em **Modelo** escolha **Padrão**
   (/cam/realmonitor?channel=1&subtype=1), **Haiz** (/avstream/channel=1/stream=0.sdp) ou **Outro** para
   digitar o caminho do manual da câmera. Quanto maior a resolução, mais processamento o equipamento gasta.
4. **Cadastrar** com a senha preenchida: ela fica guardada no equipamento e a câmera aparece nos Monitores
   (só vídeo). Use **Usar esta câmera** para a IA passar a analisá-la, sem reiniciar.
5. Escolha o modo: **Automático, Entrada ou Saída**.

### Exportar um relatório
1. **Relatórios** → escolha o período e o recorte (turno, turma).
2. **Exportar CSV** → escolha para que é o arquivo (ex.: Frequência por aluno, Busca ativa, Faltas e atrasos,
   Resumo por turma).
3. Ajuste as colunas e os filtros, escolha CSV ou Excel e clique em **Baixar**.

### Cadastrar ou atualizar em massa pela planilha (turmas, alunos, turnos, responsáveis, calendário)
1. **Escola → Planilha da base → Baixar planilha (.xlsx).**
2. No Excel (ou Google Planilhas): corrija o que precisar e acrescente linhas novas no fim de cada aba. Linha
   nova fica com a coluna **ID** vazia. Colunas cinzas são só para consulta. Não mude o nome das abas nem os
   títulos das colunas. Salve como **.xlsx**.
3. **Escola → Planilha da base → Escolha a planilha → Ver prévia.** Cada linha aparece como Novo, Alterado,
   Igual, Erro ou Ignorado, com o motivo.
4. **Gravar N mudanças.** Linhas com erro ficam de fora; corrija e importe de novo quando quiser.

Regras: só adiciona e atualiza (nada é apagado); com ID dá para renomear turno e turma (os alunos vão junto);
RM no padrão FIAP (pode digitar só os 6 números); horários HH:MM; datas DD/MM/AAAA; Sim/Não; mesmo celular em
dois alunos = o mesmo responsável (irmãos). Alunos importados entram sem rosto: o termo e as fotos continuam em
**Alunos → Adicionar foto**. Baixar: Coordenação, Direção e Administrador. Importar: Direção e Administrador.

### Criar um usuário (Direção ou Administrador)
**Administração → Usuários** → novo usuário → perfil. A pessoa entra com a senha provisória e troca no
primeiro acesso.

### Ligar o Telegram para receber alertas de segurança
- **Pela própria conta:** clique no seu nome → **Vincular Telegram** → abra o link no celular → **Iniciar**.
  O link é de uso único e vale 15 minutos. A tela confirma sozinha.
- **Pela Direção/Administrador:** **Administração → Usuários → Vincular (QR)** → a pessoa aponta o
  celular para o QR Code e toca em **Iniciar**.
- Na mesma tela de vínculo dá para desligar **Receber os alertas de segurança aqui** (ex.: dias de folga).

### Cancelar uma retirada autorizada
Ficha do aluno → **Retiradas autorizadas** → **Cancelar** → confirme. Os responsáveis são avisados no
Telegram. Use só quando o responsável pedir.

### Mudar o texto das mensagens para os responsáveis
**Escola → Integrações → Responsáveis → Mensagens que o responsável recebe** → escolha a mensagem →
escreva normalmente e use as etiquetas (Nome do aluno, Turma, Horário…), que são preenchidas sozinhas.
A prévia mostra o que chega no Telegram. **Voltar ao padrão** desfaz.

### Fazer, baixar ou restaurar backup (Administrador)
**Administração → Backup.** Restaurar substitui os dados atuais (o sistema guarda antes uma cópia do
estado atual): confirme com a direção antes.

---

## 6. Telegram

São dois bots:

| | Bot dos responsáveis | Bot da segurança |
|---|---|---|
| Quem usa | Pais e responsáveis | Equipe da escola |
| O que faz | Avisos de entrada, atraso, saída e ausência; menu com botões | Alertas de rosto obstruído com botões; comandos |
| Como entra | Convite do aluno ou compartilhando o número | Vincular Telegram (pela conta ou QR Code) |

Se a escola não configurar o bot da segurança, os alertas saem pelo bot dos responsáveis (os responsáveis
não veem esses alertas). Quando o bot da segurança é trocado, cada pessoa da equipe vincula de novo.

### 6.1 Bot dos responsáveis

**Avisos:** entrada (com foto do momento, turma e se foi no horário ou com atraso), saída (com linha
extra se for antes do fim do turno ou com retirada autorizada) e ausência.

**Menu** (`/menu`, `/start` ou qualquer mensagem):
- 🕒 **Presenças recentes:** últimos 7 dias letivos.
- 📊 **Frequência:** % do período letivo atual, faltas com datas, justificadas, atrasos e alerta abaixo de 75%.
- 🚗 **Retiradas:** autorizar quem vai buscar o aluno (hoje, amanhã ou até 30 dias à frente).
- 👥 **Responsáveis:** ver a lista; o principal adiciona ou remove.
- 📅 **Dias sem aula:** feriados e recessos dos próximos 90 dias.
- 🔔 **O que receber:** ligar ou desligar os avisos de entrada, saída e ausência.

**Retiradas:**
- O responsável informa o dia, o nome de quem vai buscar, o parentesco e, se quiser, uma foto.
- Pedido do **responsável principal** vale na hora; os outros responsáveis ficam sabendo.
- Pedido de **outro responsável** vai para o principal aprovar ou recusar. **Sem resposta, expira no
  fim do dia.**
- Até 5 pessoas por aluno por dia.
- Quem pediu ou o principal pode cancelar; a escola também, se o responsável pedir.
- A escola **não cria** retiradas: quem autoriza é sempre o responsável.

Se o responsável desligou um aviso em 🔔, a presença continua sendo registrada; o painel mostra que
os responsáveis desligaram esse aviso.

### 6.2 Bot da segurança

- Recebe quem tem conta ativa com acesso às ocorrências, vinculou o Telegram e não desligou os alertas.
  Os alertas chegam **no privado**; grupos não são usados.
- Cada alerta é um cartão com **🔍 Ver foto**, **✅ Confirmar** e **✖️ Descartar**. Quem só pode ver
  recebe apenas Ver foto.
- A foto só vai para quem toca em Ver foto. Confirmado, o relatório completo (com a foto) é enviado.
- Decidido por alguém (Telegram ou painel), o cartão some do celular de todos.
- Uma **mensagem fixada** no topo da conversa lista os alertas de hoje que aguardam decisão.
- **Lembrete** quando um alerta fica parado (padrão 5 min).
- Comandos: `/pendentes`, `/hoje` e `/historico`.
- Desativar o usuário, mudar o perfil ou desvincular interrompe os alertas na hora.

---

## 7. Regras automáticas

- **Entrada e saída:** seguem o modo da câmera. No Automático, a 1ª passagem do dia é entrada; a
  seguinte, depois do intervalo mínimo, é saída; passagens dentro do intervalo são ignoradas.
- **Atraso:** entrada depois do início das aulas + tolerância do turno do aluno.
- **Ausência:** só conta em dia letivo e depois do horário-limite do turno de cada aluno.
- **Aviso automático de ausência:** tolerância + minutos configurados (padrão 60), uma vez por aluno por
  dia, só para quem não desligou esse aviso. Falhas aparecem em **Ocorrências → Avisos não entregues**.
- **Mensagens repetidas:** há um intervalo mínimo entre avisos do mesmo aluno e entre alertas de segurança.
- **Descarte sozinho:** se o rosto aparecer depois do alerta de rosto obstruído, o alerta é descartado.
- **Dias anteriores** são fechados pela rotina diária e não mudam sozinhos; o dia de hoje é recalculado
  a cada consulta. Correções manuais e feriados cadastrados depois continuam valendo.

---

## 8. Privacidade e LGPD

- Reconhecimento facial é **dado biométrico** (dado pessoal sensível). O termo de consentimento do
  responsável é obrigatório antes de cadastrar o rosto; revogar apaga os dados faciais.
- O vídeo é analisado no equipamento da escola; a biometria não vai para a nuvem.
- O sistema guarda os dados faciais calculados, não as fotos do rosto.
- **Retenção padrão:** fotos dos alertas por 30 dias, passagens da câmera por 180 dias, foto de quem vai
  buscar apagada 7 dias depois da retirada. A frequência do dia (histórico escolar) é mantida.
- A foto do responsável é opcional e **não** entra no reconhecimento.
- Fotos enviadas pelo Telegram chegam sem os dados de localização do celular.
- No alerta de segurança, a foto só vai para quem pede para ver.
- O perfil Portaria e segurança não vê nome e celular dos responsáveis.
- Toda ação sensível fica na **auditoria**.

---

## 9. Problemas comuns

**O aluno não está sendo reconhecido.**
- Confira na lista de Alunos se ele está com **Sem termo** ou **Poucas fotos**.
- Adicione mais fotos, de frente, com luz parecida com a do local da câmera.
- Rosto pequeno (longe), contraluz, boné ou máscara atrapalham.
- Enquanto isso, registre **Presente** manualmente.
- Se acontecer com vários alunos, encaminhe ao suporte técnico.

**A câmera viu o aluno mas não registrou outra entrada ou saída.**
Ele passou dentro do **intervalo mínimo** entre entrada e saída. Em **Ao vivo → Agora na câmera** aparece
"já passou há pouco — a próxima conta em ~N min". A passagem continua visível em **Passagens na câmera**.

**O aluno foi reconhecido, mas a frequência não contou.**
O dia pode não ser letivo (feriado, recesso, fora do ano letivo ou fora dos dias da semana com aula).
Confira em **Escola → Calendário** e **Escola → Regras e retenção**.

**Alerta de "Rosto obstruído" para alguém que não estava coberto.**
A pessoa ficou perto da câmera um tempo sem mostrar o rosto (de costas, olhando para baixo). Descarte o
alerta. Se o rosto aparecer depois, o sistema descarta sozinho.

**Aluno cadastrado aparece como "Não cadastrado".**
Ângulo ruim, luz fraca ou máscara. Adicione mais fotos. Se for frequente, encaminhe.

**Presença marcada como atraso ou falta indevidamente.**
- Confira o turno da turma e a tolerância em **Escola → Turnos**.
- Confira se o dia está como feriado/recesso no **Calendário** (cadastrar depois recalcula).
- Corrija com **Presente** ou **Justificar**.

**O responsável não recebe as mensagens.**
1. Na lista de Alunos, veja se o responsável tem o ponto verde (Telegram vinculado). Se não, **Gerar convite**.
2. Use **Enviar teste** na ficha do aluno → Responsáveis → Abrir.
3. Peça para ele conferir em 🔔 **O que receber** se o aviso não está desligado.
4. Veja **Ocorrências → Avisos não entregues** e use **Reenviar**.
5. Confira se **Escola → Integrações → Avisar os responsáveis** está ligado.
6. Se o estado do sistema indicar problema no Telegram ou falta de internet, encaminhe ao suporte técnico.

**Não recebo os alertas de segurança no Telegram.**
Sua conta precisa estar vinculada (seu nome → Vincular Telegram), seu perfil precisa ter acesso às
ocorrências e **Receber os alertas de segurança aqui** precisa estar ligado. Em **Escola → Integrações →
Alertas de segurança** a coordenação vê a situação de cada pessoa.

**Recebo o alerta, mas não consigo confirmar ou descartar.**
Seu perfil só pode ver. Peça a permissão "Confirmar ou descartar alertas".

**O link do Vincular Telegram não funciona.**
Ele vale 15 minutos e só uma vez. Gere outro.

**Câmera sem imagem.**
Em **Ao vivo → Adicionar / testar câmera**, use **Testar agora**. Se falhar, confira se a câmera está
ligada e na rede. Persistindo, encaminhe ao suporte técnico.

**Aviso de relógio diferente.**
A hora do equipamento está diferente da do computador em mais de 2 minutos, o que afeta presença e
atrasos. A hora do equipamento aparece no menu do seu nome. Encaminhe ao suporte técnico.

**Esqueci a senha / acesso bloqueado.**
Depois de 5 senhas erradas em 10 minutos, o acesso fica bloqueado por 5 minutos. Se esqueceu, peça à
direção ou ao administrador para redefinir; você recebe uma senha provisória.

**Não vejo uma tela ou botão.**
Seu perfil não tem a permissão. Peça à direção ou ao administrador.

**Não vejo o celular do responsável.**
O perfil Portaria e segurança não tem acesso aos contatos. Fale com a coordenação.

**Dados com selo "fictício".**
São de demonstração. Podem ser removidos em **Administração → Dados fictícios** sem afetar os reais.

**Retirada "Aguardando o principal".**
Foi pedida por um responsável que não é o principal. O principal precisa aprovar no Telegram; sem
resposta, expira no fim do dia.

---

**Os alunos fictícios não chegam nem saem (painel parado, relatórios vazios).**
Abra **Administração → Dados fictícios** (perfil Administrador). Se aparecer "Ainda não foram criados" ou 0 alunos fictícios, clique
em **Criar dados fictícios**. Depois confira se **Simulação ao vivo** está marcada. Os horários seguem o relógio
do equipamento, sempre no horário de Brasília. Se a apresentação é à tarde e você quer mostrar o turno da noite,
use **Administração → Dados fictícios → Horário da demonstração** (ex.: 21:00).

**A importação da planilha mostra erro em algumas linhas.**
Leia o motivo na prévia (filtro **Erros**). Os mais comuns:
- **RM inválido:** use RM + 6 números (ou só os 6 números).
- **Turma "X" não existe:** cadastre a turma na aba Turmas da mesma planilha. Se a turma foi renomeada depois
  que a planilha foi baixada, baixe a planilha de novo.
- **ID não existe:** a linha é nova; apague o ID.
- **Nome repetido / RM repetido:** já existe outro cadastro com esse nome ou RM.
- **Já tem outro responsável principal:** marque **Sim** em Principal em só uma linha por aluno.
As outras linhas podem ser gravadas mesmo assim; as com erro ficam de fora.

**A planilha não abre na importação ("não é uma planilha .xlsx").**
Salve no Excel como **Pasta de Trabalho do Excel (.xlsx)**. CSV e .xls antigo não são aceitos. No Google
Planilhas: Arquivo → Fazer download → Microsoft Excel (.xlsx).

**O aluno importado pela planilha não é reconhecido na câmera.**
A planilha não traz rosto. Registre o termo de consentimento e adicione as fotos em **Alunos → ficha do
aluno → Rosto e termo de consentimento**.

**A aba Webcam do cadastro diz que o navegador bloqueou a câmera.**
O navegador só libera a webcam quando o painel é aberto por https ou pelo endereço do próprio computador. No
computador que tem o repasse do painel, abra por **http://127.0.0.1** com a mesma porta (ex.: http://127.0.0.1:18005)
e entre de novo. Em outro computador, use **Câmera RealGuard** (a imagem da câmera do sistema) ou envie uma foto.
Se o aviso for de permissão, clique no ícone da câmera na barra de endereço e permita.

## 10. Quando encaminhar para o especialista

Ofereça **Falar com especialista** quando:

- a resposta não está neste documento;
- o usuário relata erro, tela travada, dados sumidos ou comportamento estranho;
- câmera sem imagem, relógio diferente ou Telegram com problema que ele não consegue resolver;
- reconhecimento falhando com vários alunos;
- pedido de restaurar backup, instalar, atualizar ou mexer em configurações técnicas do equipamento;
- perguntas sobre código, servidor ou funcionamento interno do sistema;
- dúvida **jurídica** (LGPD, termo, pedido de exclusão de dados de um titular);
- incidente de segurança real em andamento (oriente primeiro a seguir o protocolo da escola e acionar
  a portaria);
- o usuário pede para falar com uma pessoa.

---

## 11. Perguntas frequentes

**O RealGuard grava o vídeo?** Não há gravação contínua. Ficam as fotos dos alertas (pelo prazo de
retenção) e o registro das passagens.

**Funciona sem internet?** Câmera, reconhecimento, frequência e painel funcionam sem internet. Só o
Telegram e este assistente precisam dela. Sem internet, o assistente mostra as perguntas frequentes.

**Dá para cadastrar muitos alunos de uma vez?** Sim, pela **Escola → Planilha da base**: baixe, acrescente as
linhas no Excel e importe. O mesmo vale para turmas, turnos, responsáveis e o calendário.

**A importação da planilha apaga alguma coisa?** Não. Ela só adiciona e atualiza. Para excluir um aluno ou uma
turma, use o painel.

**Como tiro só a lista de alunos com frequência baixa?** **Relatórios → Exportar CSV → Busca ativa.** Sai só
quem está abaixo de 75% ou em atenção, do menor para o maior, com o contato do responsável.

**O sistema libera ou bloqueia a saída do aluno?** Não. Ele registra e avisa; a decisão é da portaria.

**A escola pode autorizar uma retirada?** Não. Só os responsáveis, pelo Telegram. A escola confere e pode
cancelar a pedido do responsável.

**Os pais acessam o painel?** Não. Tudo dos responsáveis acontece no bot do Telegram.

**Quantas câmeras posso cadastrar?** Várias. O RealGuard analisa uma por vez, e dá para trocar sem reiniciar.

**Feriado cadastrado depois apaga as faltas daquele dia?** Sim, a frequência daquele dia é recalculada.

**Quem decidiu um alerta?** Em **Ocorrências → Histórico de alertas** ou **Administração → Auditoria**.

**Posso excluir um aluno?** Sim, na ficha → **Excluir aluno** (precisa de permissão). O cadastro e os
dados faciais são apagados; o histórico de presenças fica.

**Existe perfil de professor?** Não entre os padrões. A escola pode criar um perfil personalizado em
**Administração → Perfis de acesso**.

**Como falo com uma pessoa do suporte?** No assistente, toque em **Falar com especialista**, descreva o
que precisa e envie. O pedido chega à equipe de suporte com um número de protocolo.

**O assistente vê os dados da escola?** Não. Ele só conhece o funcionamento do RealGuard. Não digite
nomes de alunos, telefones, documentos ou senhas na conversa.

**Por que o sistema não aceita o RM que digitei?** O RM precisa ter o padrão RM + 6 números (ex.: RM566217).
Pode digitar só os 6 números que o sistema completa o "RM". RM com outra quantidade de números é recusado.

---

## 12. Vocabulário: como as pessoas chamam as coisas

Quando a pessoa usar uma destas palavras, entenda como o termo do RealGuard ao lado.

| A pessoa diz | No RealGuard é |
|---|---|
| chamada, lista de presença, presença do dia, faltas | frequência (situação do dia) — **Visão geral**, ficha do aluno, **Relatórios** |
| pai, mãe, família, tutor | responsável |
| bot, zap do bot, aplicativo dos pais, mensagem para os pais | Telegram (bot dos responsáveis) |
| alerta, alarme, ocorrência, "pessoa estranha", "encapuzado" | alerta de rosto obstruído — **Ocorrências** |
| portão, catraca, entrada, câmera da portaria | câmera (modo Entrada, Saída ou Automático) — **Ao vivo** |
| reconhecimento, biometria, foto do rosto, "a câmera não me conhece" | rosto e termo de consentimento — ficha do aluno |
| liberar, buscar, "quem pode pegar o aluno", saída com outra pessoa | retirada autorizada |
| matrícula, número do aluno, registro | RM (ex.: RM566217) |
| sala, classe, série | turma (ex.: 2ECR) |
| atraso, chegou tarde | Atrasado (depois da tolerância do turno) |
| horário do turno, hora de entrada, tolerância | **Escola → Turnos** |
| feriado, emenda, recesso, dia sem aula, sábado letivo | **Escola → Calendário** |
| login, conta, senha, perfil, permissão | acesso e perfis — **Administração → Usuários** / **Perfis de acesso** |
| exportar, planilha, Excel | **Exportar CSV** em **Relatórios** |
| tela de câmeras, CFTV, mosaico | **Ao vivo → Monitores** |
| sininho | sino de alertas (topo) |
| suporte, técnico, falar com alguém, atendente | **Falar com especialista** |
