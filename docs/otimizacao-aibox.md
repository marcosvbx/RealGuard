# Otimizacao da IA BOX - 21/10/2026

Alteracoes em captura, pipeline, reconhecimento facial e previas. SNPE/Hexagon nao foram integrados.

## Configuracao ativa

- CAMERA_CAPTURE_BACKEND=opencv: melhor resultado medido nas duas cameras atuais.
- ORT_ALLOW_SPINNING=0; YOLO_THREADS=2; FACE_THREADS=2.
- OpenCV, BLAS e OMP limitados a uma thread por padrao antes dos imports.
- YOLO original 640 preservado; variantes 512/416/320 disponiveis em models/.
- Fila facial: validade de 1 segundo, prioridade para nao identificados, descarte de tracks/base vencidos.
- Recortes faciais usam a resolucao original da captura. Embeddings da base sao normalizados em cache; somente o maior rosto do recorte gera embedding.
- Sem espectador: nao desenha nem codifica o video. Evidencias continuam sendo geradas sob demanda.
- Previas: um JPEG por camera/quadro compartilhado por todos os espectadores.
- read_latest entrega quadro e sequencia atomicamente; consumidores nao devem modificar a matriz compartilhada.

## Resultados e limites

73 testes unitarios/integracao selecionados passaram. Smoke com camera, YOLO, InsightFace e JPEG passou, sem escrever eventos ou enviar mensagens. Suite completa com pytest nao executada: pacote ausente e DNS da BOX indisponivel para instalar.

Captura, amostras de 6 segundos, maquina compartilhada:

| Camera | OpenCV | GStreamer qtivdec |
| --- | --- | --- |
| Principal 704x480 | 30 FPS / 14,2% CPU | 29,8 FPS / 30,4% CPU |
| Secundaria 2880x1620 | 19,8 FPS / 69,6% CPU | 6,5 FPS / 83,4% CPU |

A saida qtivdec da secundaria reportou 2880x1632 (alinhamento/padding), exigindo validacao de crop antes de uso. Hardware decode esta implementado como opcao, mas NAO ativado: os testes nao mostraram beneficio. CAMERA_CAPTURE_BACKEND=gstreamer tenta H264/H265 e retorna a OpenCV em falha. Requer dispositivos video acessiveis. grupo05 foi adicionado a video com autorizacao do usuario; nova sessao SSH necessaria.

YOLO: variantes menores aceleraram inferencia, mas mudaram as deteccoes em frames reais. 320 perdeu varias pessoas; 416 e 512 tambem nao conservaram todas as deteccoes. Nao ativadas. A especializacao 640 reproduziu exatamente a saida do original (erro maximo 0). Isto verifica a transformacao, nao a acuracia das outras resolucoes.

Em dois benchmarks curtos, duas threads sem spinning consumiram menos CPU que duas com spinning. Nao e promessa de ganho geral: carga de outros grupos varia.

Filtros FACE_MIN_CROP_WIDTH, FACE_MIN_CROP_HEIGHT e FACE_MIN_SHARPNESS ficam em 0 ate calibracao. Descartar imagem por qualidade nao conta como rosto obstruido. Nenhum limiar de identidade foi reduzido.

## Reproduzir

Executar na raiz com .venv/bin/python:
- scripts/benchmark_capture.py opencv [id_camera]
- scripts/benchmark_capture.py gstreamer [id_camera]
- scripts/benchmark_edge.py
- scripts/smoke_edge.py (sem eventos, banco ou notificacoes)
- -m unittest tests.test_edge_optimizations tests.test_melhorias_processamento tests.test_face_integration tests.test_pipeline_embeddings_integration tests.test_pipeline_obstructed tests.test_identity_manager tests.test_integration tests.test_rules tests.test_rules_integration tests.test_zones_integration

Para experimentar outro modelo, MODEL_PATH pode apontar para models/yolov8n_512.onnx, 416 ou 320. Validar pessoas pequenas/distantes e reconhecimento na cena real antes de substituir o original. Nao basta reduzir PROCESS_MAX_WIDTH.

Nao alteramos resolucao, FPS ou codec no firmware das cameras. A principal ja usa substream 704x480; reduzir mais pode prejudicar rostos. A secundaria usa stream de alta resolucao; um substream precisaria ser validado para o enquadramento/qualidade facial antes de trocar a fonte.

Backup de fontes anterior: /home/grupo05/eduvision/realguard-before-opt.rthyl5/source.tar.gz (inclui configuracao privada). Nao restaurar por cima de um processo em execucao. O backup local do usuario tambem permanece disponivel.

## Ativacao

Reiniciado apos os testes, PID inicial 26289. Painel HTTP /login respondeu 200 na porta 18005; /api/status sem autenticacao continuou respondendo 401. YOLO e InsightFace carregados, camera conectada, registro de presenca observado no log. Processo antigo encerrou apos segunda solicitacao de interrupcao por conexoes em andamento. Log da nova execucao: logs/console-optimized.log.

## Nitidez e distância — 21/10/2026

Backup anterior: /home/grupo05/eduvision/realguard-before-distance-20261021.tar.gz.
A câmera 192.168.50.108 usava substream 704x480/30 FPS. O cadastro e o
CAMERA_RTSP_PATH agora selecionam subtype=0: 1920x1080/30 FPS, H.265.
Nenhuma zona estava cadastrada no momento da troca. A câmera .109 mantém
seu stream nativo 2880x1620, observado em aproximadamente 20 FPS.
#N
o foi confirmada capacidade física de 60 FPS nas câmeras.

O dashboard principal publica até 1280x720, JPEG qualidade 85; seu teto
 60 FPS, sem criar quadros duplicados. A codificação usa uma thread própria
com um único quadro pendente, substituído pelo mais recente. As prévias
usam até 1280 pixels, qualidade 85 e teto 30 FPS, configuráveis com
PREVIEW_MAX_WIDTH, PREVIEW_JPEG_QUALITY e PREVIEW_MAX_FPS. Clientes compartilham
JPEGs e não reenviam o mesmo quadro enquanto aguardam um novo.

O reconhecimento preserva recortes da imagem nativa. Se um recorte maior
que 320 pixels não tiver rosto detectado, tenta novamente com entrada 640
(FACE_RETRY_DET_SIZE). O limiar de identidade e a confirmação por múltiplas
evidências foram preservados. A API informa dimensões do último rosto,
confiana do detector e segunda tentativa; isso não mede distância em metros.
FPS de vídeo significa JPEGs publicados pelo servidor, não FPS renderizado
no navegador nem número de inferências por segundo.

#Valida
o: 76 testes unitários passaram; compileall app/api passou. Teste
integrado sem envio de mensagens ou gravação de presenças: captura 29,9 FPS,
loop 29,6 FPS, vídeo 24,6 FPS, JPEG 1280x720 em 5,1 ms (cena/carga variáveis).
Prévia .109: 18,2 quadros únicos/s em 1280x720 durante 6 segundos.
O teste da IA usou galeria vazia: valida processamento, não acurácia biométrica.
Rostos observados de 14x16 a 19x23 pixels indicam pouco detalhe na cena.
Ainda falta teste presencial com pessoa cadastrada e distâncias marcadas
para quantificar alcance, falsos positivos e falsos negativos. Não há promessa
de reconhecimento além de 3 m nem comprovação de latência ponta a ponta.
Serviço reiniciado com logs em logs/console-distance.log.

Sintaxe JavaScript da função renderLive validada com Node.js; não foi realizado teste visual autenticado no navegador.
