"""Sinaliza produtores antes de o Uvicorn aguardar clientes de streaming."""
import uvicorn


class RealGuardServer(uvicorn.Server):
    def handle_exit(self, sig, frame):
        from app.engine import engine
        from app.camera.previews import previews
        engine.request_stop()
        previews.stop_all()
        super().handle_exit(sig, frame)
