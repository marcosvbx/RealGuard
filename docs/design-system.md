# RealGuard — sistema visual (resumo para quem for mexer na interface)

Tudo vive em `web/index.html` (variáveis em `:root` e a "camada final de design" no fim do `<style>`).
`login.html` e `monitores.html` usam os mesmos tokens e fontes.

## Cores
| Token | Valor | Uso |
|---|---|---|
| `--bg` | `#070e1d` | fundo azul-noite |
| `--surface` / `--surface-2` | `#0c1729` / `#122038` | cartões / campos, linhas de lista |
| `--line` / `--line-2` | azul a 8,5% / 16% | divisórias / bordas de cartão |
| `--ink` / `--ink-2` / `--muted` | `#eef2f8` / `#b3bfd2` / `#8592a8` | texto principal / secundário / apoio |
| `--accent` | `#3987e5` | dado e interação (links, filtros ativos, gráficos) |
| `--gold` | `#d6b25e` | **só** marca (pessoa do escudo) e botão principal |
| `--good` / `--warn` / `--bad` | verde / âmbar / vermelho | **só** estado — nunca decoração |

Séries dos gráficos (validadas para daltonismo): azul = no horário/entrada, laranja = atraso, verde-água = saída.

## Tipografia (arquivos em `web/fonts/`, licença SIL OFL, sem internet)
- **Poppins** 300–700: logotipo, título da página, título dos cartões e números grandes.
- **Archivo** (variável): texto, tabelas, botões. Números com `tabular-nums`.
- Monoespaçada só para códigos (RM, token, link).
- Rótulos em caixa normal (nada de MAIÚSCULAS espaçadas).

## Forma
- Raio 14px nos blocos (`--radius`), 10px nos controles (`--radius-sm`), pílula nos filtros.
- Indicadores: borda neutra; a cor do estado aparece no ícone e na barra.
- Foco visível em tudo que é clicável (contorno azul de 2px).

## Movimento
- Uma entrada por tela (cartões sobem em sequência ao abrir a aba); atualizações automáticas não animam.
- Hover com "respiro" só no que é clicável (indicadores, turmas); `prefers-reduced-motion` desliga tudo.

## Texto
- Frases curtas, do ponto de vista de quem usa ("Não chegaram até o horário-limite"), números no formato brasileiro.
- Avisos de configuração aparecem só na Visão geral e podem ser dispensados; problemas de câmera/IA aparecem em todas as telas.
