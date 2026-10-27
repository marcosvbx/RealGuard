"""
Catálogo de permissões e perfis padrão do RealGuard.

Cada permissão é uma ação concreta do sistema. Os perfis padrão são
definidos aqui (no código) e sincronizados no banco a cada subida; perfis
personalizados são criados pelo administrador marcando, numa tabela, o que
cada um pode ver/alterar — sempre a partir deste mesmo catálogo.
"""

from typing import Dict, List, Tuple

# (chave, área, descrição) — a ordem é a da tabela na tela de Perfis.
PERMISSIONS: List[Tuple[str, str, str]] = [
    ("overview.view", "Visão geral", "Ver os indicadores do dia"),
    ("live.view", "Ao vivo", "Ver o vídeo, os contadores e o Painel de monitores"),
    ("monitors.diagnostics", "Ao vivo", "Ver o diagnóstico do Painel de monitores (CPU, memória, tempos da IA)"),
    ("live.mode", "Ao vivo", "Trocar o modo da câmera (entrada/saída)"),
    ("cameras.view", "Câmeras", "Ver as câmeras cadastradas e o estado de cada uma"),
    ("cameras.manage", "Câmeras", "Cadastrar, testar e ativar câmeras"),
    ("events.view", "Ocorrências", "Ver os alertas e a foto do momento"),
    ("events.decide", "Ocorrências", "Confirmar ou descartar alertas"),
    ("events.delete", "Ocorrências", "Excluir ocorrências já decididas do histórico (apaga a foto)"),
    ("absences.notify", "Ocorrências", "Avisar responsáveis sobre ausências"),
    ("reports.view", "Relatórios", "Ver os relatórios"),
    ("reports.export", "Relatórios", "Exportar relatórios em CSV"),
    ("students.view", "Alunos", "Ver a lista de alunos"),
    ("students.edit", "Alunos", "Cadastrar e editar alunos e adicionar fotos do rosto"),
    ("students.delete", "Alunos", "Excluir alunos (apaga os vetores faciais)"),
    ("attendance.mark", "Frequência", "Marcar presença manual e corrigir horário de entrada"),
    ("attendance.justify", "Frequência", "Justificar faltas e desfazer correções de frequência"),
    ("guardians.view", "Responsáveis", "Ver nome e celular do responsável"),
    ("guardians.telegram", "Responsáveis", "Convidar, testar e desvincular o Telegram do responsável"),
    ("school.manage", "Escola", "Configurar turnos, turmas, calendário, regras de presença, retenção e termo"),
    ("base.export", "Escola", "Exportar a planilha da base (turnos, turmas, alunos, responsáveis e calendário)"),
    ("base.import", "Escola", "Importar a planilha da base para cadastrar e atualizar em massa"),
    ("telegram.manage", "Escola", "Configurar o bot do Telegram: token, destinatários de alertas e mensagem"),
    ("users.manage", "Administração", "Criar usuários, redefinir senhas e desativar acessos"),
    ("roles.manage", "Administração", "Criar e editar perfis personalizados"),
    ("audit.view", "Administração", "Ver o registro de auditoria"),
    ("backup.manage", "Administração", "Criar, baixar e restaurar backups"),
    ("telegram.contacts", "Administração", "Ver quem falou com o bot (vínculo manual de Chat ID)"),
    ("sample.manage", "Administração", "Remover ou regenerar os dados fictícios"),
    ("system.configure", "Administração", "Abrir as configurações de inicialização e reiniciar o RealGuard"),
    ("system.stop", "Administração", "Encerrar completamente o RealGuard"),
    ("assistant.use", "Assistente", "Usar o assistente virtual (dúvidas sobre o RealGuard e pedido ao especialista)"),
    ("assistant.manage", "Assistente", "Configurar o assistente: chave do Gemini e Telegram do suporte"),
]
ALL_PERMISSIONS = [p[0] for p in PERMISSIONS]
PERMISSION_SET = frozenset(ALL_PERMISSIONS)

ROLE_ADMIN = "admin"
ROLE_DIRECAO = "direcao"
ROLE_GESTOR = "gestor"          # exibido como "Coordenação" (a chave antiga fica: usuários existentes não mudam)
ROLE_OPERADOR = "operador"      # exibido como "Portaria e segurança"
ROLE_SEGURANCA = "seguranca"    # perfil antigo: unificado na Portaria (migração 13)

# Hierarquia pensada para a escola (do mais amplo ao mais restrito):
#   Administrador (TI / quem instala) > Direção > Coordenação > Portaria e segurança
# Na escola, quem fica na portaria é quem olha as câmeras: um perfil só para os dois papéis.
# Só o Administrador mexe em perfis, backup e dados fictícios (coisas de instalação).

_GESTOR = [      # Coordenação pedagógica: o dia a dia da escola, inclusive câmeras e Telegram
    "overview.view", "live.view", "monitors.diagnostics", "live.mode", "cameras.view", "cameras.manage",
    "events.view", "events.decide", "absences.notify",
    "reports.view", "reports.export",
    "students.view", "students.edit", "students.delete",
    "attendance.mark", "attendance.justify",
    "guardians.view", "guardians.telegram",
    "school.manage", "base.export", "telegram.manage", "telegram.contacts",
    "audit.view",
    "assistant.use",
]
_DIRECAO = _GESTOR + [    # Direção: tudo da Coordenação + equipe (usuários e QR do Telegram) e excluir ocorrências
    "users.manage", "events.delete", "base.import", "system.configure", "system.stop",
]
_OPERADOR = [    # Portaria e segurança: câmeras, alertas, entrada e saída — quem fica no portão
    "overview.view", "live.view", "monitors.diagnostics", "live.mode", "cameras.view",
    "events.view", "events.decide", "absences.notify",
    "students.view", "attendance.mark",
    "assistant.use",
]

# chave -> (nome, descrição, permissões). Não editáveis pela tela; a ordem é a da hierarquia.
SYSTEM_ROLES: Dict[str, Tuple[str, str, List[str]]] = {
    ROLE_ADMIN: ("Administrador", "TI / quem instala o sistema: acesso total, inclusive perfis, backup, dados "
                                  "fictícios e o assistente virtual (chave do Gemini e Telegram do suporte).",
                 ALL_PERMISSIONS),
    ROLE_DIRECAO: ("Direção", "Tudo da Coordenação e mais a equipe: cadastra usuários, vincula o Telegram de cada um "
                              "(QR Code) e pode excluir ocorrências. Sem perfis, backup e dados fictícios.",
                   _DIRECAO),
    ROLE_GESTOR: ("Coordenação", "Dia a dia da escola: alunos, responsáveis, frequência, relatórios, câmeras, "
                                 "calendário e Telegram. Não cadastra usuários.", _GESTOR),
    ROLE_OPERADOR: ("Portaria e segurança", "Quem fica na entrada: câmeras ao vivo e Painel de monitores, alertas "
                                            "(confirmar ou descartar), presença manual e aviso de ausência. Não vê "
                                            "contatos dos responsáveis nem relatórios.", _OPERADOR),
}


def clean_permissions(values) -> List[str]:
    """Mantém só chaves conhecidas, sem repetição, na ordem do catálogo."""
    wanted = set(values or [])
    return [p for p in ALL_PERMISSIONS if p in wanted]


def catalog() -> List[Dict[str, str]]:
    return [{"chave": k, "area": a, "descricao": d} for k, a, d in PERMISSIONS]
