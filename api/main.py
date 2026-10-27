import logging
import os
from contextlib import asynccontextmanager

# Mesmo ajuste usado no EduVision: limita quantos núcleos numpy/onnxruntime
# tentam usar por chamada. PRECISA vir antes de qualquer import que carregue
# numpy/onnxruntime (abaixo, via api.routes/app.engine -> app.pipeline ->
# app.detection.yolo e app.recognition.insightface). Sem isso, o ONNX
# Runtime tende a usar todos os núcleos da AIBOX por inferência, brigando
# por CPU com a thread de captura RTSP e a de reconhecimento facial.
os.environ.setdefault("OMP_NUM_THREADS", "3")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "3")

from fastapi import FastAPI

from config.logging_setup import attach_server_logs, setup_logging

LOG_FILE = setup_logging()

from api.routes import admin, assistant, auth, cameras, dashboard, events, guardians, integrations, monitors, notifications, pickups, planilha, reports, school, students, system  # noqa: E402
from api.security import SecurityMiddleware  # noqa: E402
from app.engine import engine  # noqa: E402
from app.maintenance import maintenance  # noqa: E402
from app.security_alerts import ReminderLoop, SecurityPanel  # noqa: E402
from app.simulation import SimulationLoop  # noqa: E402
from app.absence_auto import AbsenceNoticeLoop  # noqa: E402
from config.settings import check_env_file_permissions  # noqa: E402
from database.migrations import initialize_database  # noqa: E402

logger = logging.getLogger("realguard")
reminders = ReminderLoop(engine)
security_panel = SecurityPanel(engine)
simulation = SimulationLoop(engine)
absence_notices = AbsenceNoticeLoop(engine)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    attach_server_logs()
    if LOG_FILE:
        logger.info(f"Log em arquivo: {LOG_FILE}")
    warning = check_env_file_permissions()
    if warning:
        logger.warning(warning)
    # Banco migrado ANTES de aceitar login (o motor também migra, de forma idempotente).
    try:
        initialize_database(engine.settings.camera_id, engine.settings.camera_name)
    except Exception as e:
        logger.error(f"Falha ao preparar o banco: {e}")
    # A IA sobe junto com o servidor e não depende de nenhuma página aberta.
    from app import arena as arena_mode
    if arena_mode.enabled():
        arena_mode.start()
    engine.start()
    if not arena_mode.enabled():
        maintenance.start()
        reminders.start()
        security_panel.start()
        simulation.start()
        absence_notices.start()
    try:
        yield
    finally:
        engine.request_stop()
        absence_notices.stop()
        simulation.stop()
        from app.camera.previews import previews
        previews.stop_all()
        security_panel.stop()
        reminders.stop()
        maintenance.stop()
        engine.stop()
        if arena_mode.enabled():
            arena_mode.stop()


app = FastAPI(title="RealGuard", version="2.1.0", lifespan=lifespan,
              docs_url=None, redoc_url=None, openapi_url=None)   # sem documentação pública da API
app.add_middleware(SecurityMiddleware)

from api.routes import arena as arena_routes
app.include_router(arena_routes.router)
app.include_router(auth.router)
app.include_router(system.router)
app.include_router(students.router)
app.include_router(events.router)
app.include_router(dashboard.router)
app.include_router(reports.router)
app.include_router(cameras.router)
app.include_router(admin.router)
app.include_router(school.router)
app.include_router(school.attendance_router)
app.include_router(guardians.router)
app.include_router(integrations.router)
app.include_router(monitors.router)
app.include_router(pickups.router)
app.include_router(notifications.router)
app.include_router(assistant.router)
app.include_router(planilha.router)
