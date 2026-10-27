"""
Avisos aos responsáveis: histórico (API), o que precisa de atenção em Ocorrências
(falhas de envio, alunos sem Telegram), reenvio e os modelos de mensagem
(Escola → Integrações → Bot dos responsáveis → "Como o responsável recebe").
"""

from config.clock import date, datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request

from api.security import audit, require, require_any
from app.notifications.messages import absence_message, presence_message
from database import notifications

router = APIRouter(prefix="/api/notifications", tags=["notifications"])

_SAMPLE = {"name": "Lucas Mendes", "class_name": "7º A", "registration_number": "1042"}


def _models() -> dict:
    """Exemplos gerados pelas MESMAS funções que o bot usa — o que aparece aqui é o que o responsável recebe."""
    t = datetime.now().replace(hour=7, minute=38)
    return {
        "ENTRY": presence_message(_SAMPLE, "Portão principal", "ENTRY", "07:45", now=t),
        "LATE": presence_message(_SAMPLE, "Portão principal", "ENTRY", "07:45", now=t.replace(hour=8, minute=12)),
        "EXIT": presence_message(_SAMPLE, "Portão principal", "EXIT", now=t.replace(hour=12, minute=25)),
        # saída antes do fim do turno: a MESMA mensagem automática de saída, com a linha de aviso
        "EARLY_EXIT": presence_message(_SAMPLE, "Portão principal", "EXIT", now=t.replace(hour=10, minute=5))
        + "\n🟠 <b>Antes do fim do turno</b> (aulas até 12:00)",
        "ABSENCE": absence_message(_SAMPLE, "07:45"),
    }


@router.get("")
def list_notifications(day: Optional[str] = None, kind: str = "", falhas: bool = False, q: str = "",
                       user=Depends(require_any("absences.notify", "reports.view"))):
    try:
        d = date.fromisoformat(day) if day else date.today()
    except ValueError:
        raise HTTPException(status_code=400, detail="Data inválida (use AAAA-MM-DD).")
    return {"dia": d.isoformat(), "resumo": notifications.summary(d), "tipos": notifications.KINDS,
            "itens": notifications.list_log(d, kind=kind, failed_only=falhas, q=q.strip()), "modelos": _models()}


@router.get("/modelos")
def models(user=Depends(require_any("absences.notify", "reports.view", "school.manage"))):
    return _models()


@router.get("/atencao")
def attention(user=Depends(require_any("absences.notify", "reports.view"))):
    """Ocorrências → Precisa de atenção: avisos que falharam hoje e alunos sem Telegram."""
    return {"falhas": notifications.open_failures(), "sem_telegram": notifications.students_without_telegram(),
            "tipos": notifications.KINDS}


@router.post("/{log_id}/reenviar")
def resend(log_id: int, request: Request, user=Depends(require("absences.notify"))):
    """Reenvia ao MESMO responsável um aviso que falhou (o texto é refeito com o horário original)."""
    from app.engine import engine
    from database import repository, school
    n = notifications.get(log_id)
    if not n:
        raise HTTPException(status_code=404, detail="Aviso não encontrado.")
    student = repository.get_student(n["student_id"]) if n.get("student_id") else None
    if not student:
        raise HTTPException(status_code=400, detail="O aluno deste aviso não existe mais.")
    if not n.get("chat_id"):
        raise HTTPException(status_code=400, detail="Este responsável não tem Telegram vinculado.")
    deadline = school.deadline_for_class(student.get("class_name") or "") or engine.settings.entry_tolerance
    when = datetime.strptime(n["created_at"][:19], "%Y-%m-%d %H:%M:%S")
    kind = n["kind"]
    if kind == "ABSENCE":
        text = absence_message(student, deadline)
    elif kind in ("ENTRY", "LATE"):
        text = presence_message(student, engine.settings.camera_name, "ENTRY", deadline, now=when)
    else:
        text = presence_message(student, engine.settings.camera_name, "EXIT", now=when)
    ok, info = engine.notifier.send_message(n["chat_id"], text, "HTML")
    notifications.log(student["id"], student.get("name", ""), kind, chat_id=n["chat_id"],
                      status=notifications.SENT if ok else notifications.FAILED, detail="" if ok else info,
                      sent_by=user.get("name") or user.get("username") or "")
    if not ok:
        raise HTTPException(status_code=502, detail=f"O Telegram recusou de novo: {info}")
    audit(request, user, "aviso_reenviado", "aluno", student["id"], notifications.KINDS.get(kind, kind))
    return {"status": "success", "message": "Aviso reenviado."}
