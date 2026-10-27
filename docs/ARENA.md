# Apresentação Arena

No Windows, abra `RealGuard.exe` e escolha **Homologação** na janela de configuração. Na IA BOX, use `iniciar.sh` e selecione o modo Arena no terminal.
Também é possível executar `iniciar.bat --arena` ou `sh iniciar.sh --arena`.
`--complete` seleciona o RealGuard completo. Sem terminal interativo, o padrão é
completo; serviços podem usar `--arena --no-setup` explicitamente.

O navegador padrão abre quando o servidor começa a aceitar conexões, mostrando
o login se necessário. Use `--no-browser` para desativar. Na IA BOX sem ambiente
gráfico, abra o endereço da BOX no notebook: um processo SSH não pode controlar
o navegador do computador cliente automaticamente.

## Cenário

O **mesmo dashboard original** abre em Ao vivo. Visão geral, Ao vivo, Alunos,
Ocorrências, Escola (Turnos, Turmas e Calendário) e cadastro/seleção de câmeras continuam disponíveis conforme as
permissões do usuário. Na lista de presentes, use **Definir turma** para associar
a turma ativa cadastrada em Escola à câmera ativa. A associação é persistida para a próxima execução
e acompanha a renomeação da turma. Escola usa os mesmos cadastros do modo completo.

O relógio da sessão começa às **19h20** na inicialização do servidor e avança
normalmente; recarregar o navegador não o reinicia. Registros da Arena guardam
horário da demonstração e horário real, sem alterar o relógio do equipamento ou
as regras de frequência da versão completa.

Os cadastros/embeddings reais são compartilhados. Dados fictícios ficam fora da
Arena, sem serem apagados da apresentação completa. Registros da Arena ficam na
tabela `arena_events`, separados das presenças e ocorrências escolares. O painel
consulta a sessão atual. Mudanças de visibilidade, identidade, obstrução e turma
são registradas; métricas são amostradas a cada cinco segundos quando consultadas
pelo dashboard/API. A gravação é feita em thread independente do vídeo.

## Contagem

Indicadores e gráfico mostram as mesmas cinco métricas: pessoas na sala, da turma,
de outra turma, obstruídos e não identificado. Não identificado inclui toda pessoa
visível sem identidade confirmada, inclusive quem ainda está sendo analisado.
As colunas não são parcelas somáveis: total inclui todos e obstrução pode coexistir
com identidade conhecida. O alerta de não identificado só é aberto quando a IA
confirma o estado desconhecido, não durante a identificação inicial.

Ocorrências mostra primeiro os alertas pendentes de rosto obstruído e pessoa
não identificada, com Confirmar/Descartar para usuários com permissão de decisão.
Cada condição gera uma ocorrência por track no acompanhamento da câmera, sem
duplicação a cada frame. Sair do campo não resolve automaticamente o alerta.
As decisões são auditadas e os logs técnicos aparecem abaixo do histórico de
ocorrências resolvidas. Na Visão geral, as atividades não incluem amostras de FPS.
Capturas ficam disponíveis somente enquanto a ocorrência estiver pendente.
Confirmar ou descartar remove a imagem da memória e do banco; o registro textual
e a decisão permanecem. A inicialização também remove capturas de ocorrências
resolvidas por versões anteriores.

Apenas tracks associados a uma detecção nova contam como visíveis. Na próxima
detecção sem aquela pessoa, ela sai da lista; não há temporizador adicional de
saída. Isso significa que falhas/oclusões da detecção também podem tirar alguém
da contagem. Não é uma afirmação de saída física pela porta. Câmera/IA sem
observação há mais de três segundos mostram contagem indisponível.

Identificados da turma, de outra turma, sem turma definida, não cadastrados e
em identificação são categorias da composição da sala. Obstrução é uma condição
adicional: pode coexistir com uma identidade já conhecida no mesmo track.
Três tentativas sem rosto durante pelo menos três segundos geram essa condição;
ela também pode indicar rosto virado ou fora da qualidade necessária, não prova
que alguém deliberadamente cobriu a face. Quadros recusados por qualidade não
são evidência de obstrução. Nova câmera/reinício do pipeline limpa identidades
visuais da sala para não reaproveitar IDs de tracking.

## Foto principal

No formulário original de cadastro ou de adicionar foto, marque **Salvar esta
imagem como foto principal**, com autorização. A imagem é reduzida a no máximo
480 px e servida por uma rota autenticada, sem cache. Revogar consentimento ou
excluir o cadastro remove a foto principal. A foto não modifica os thresholds
nem substitui os embeddings de reconhecimento.

Cadastros antigos têm somente embeddings, dos quais não se pode recuperar a
imagem original. Eles continuam sendo reconhecidos e exibem avatar até que uma
foto principal seja enviada. A foto principal não é uma captura de vigilância.

## Validação

A pasta de código é compartilhável entre Windows e Linux/IA BOX; o ambiente
virtual e os pacotes nativos devem ser instalados para cada plataforma, não copiados.
O modo completo usa um relógio de negócio compatível com Windows para simulação,
presenças e relatórios. Linux preserva o ajuste via TZ sem somar o deslocamento
duas vezes. Autenticação e timeouts continuam usando o tempo real.

`tests/test_arena.py`: relógio, turmas, obstrução com identidade, saída do campo,
falha/troca de câmera, persistência, autenticação de fotos e isolamento de modos.
`tests/test_shutdown.py`: encerramento com stream HTTP aberto. Testes usam banco
temporário. A precisão na sala real depende do enquadramento definido pela banca.
