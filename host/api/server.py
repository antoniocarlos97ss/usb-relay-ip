import logging
import threading
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from host.core import config_manager
from host.api.routes.devices import router as devices_router
from host.api.routes.health import router as health_router
from host.api.routes.share import router as share_router

logger = logging.getLogger(__name__)

_server_thread: threading.Thread | None = None
_should_stop = False


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


app = FastAPI(
    title="USBRelay Host",
    version="1.0.0",
    description="USBRelay Host REST API",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def api_key_middleware(request: Request, call_next):
    config = config_manager.load_config()
    api_key = config.api_key

    if api_key and api_key.strip():
        if request.url.path.startswith("/api/v1"):
            request_key = request.headers.get("X-API-Key", "")
            if request_key != api_key:
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Unauthorized. Provide a valid X-API-Key header."},
                )

    response = await call_next(request)
    return response


app.include_router(devices_router, prefix="/api/v1")
app.include_router(health_router, prefix="/api/v1")
app.include_router(share_router, prefix="/api/v1")


def _run_server(host: str, port: int):
    global _should_stop
    server_config = uvicorn.Config(
        app=app,
        host=host,
        port=port,
        log_level="info",
        log_config=None,
    )
    server = uvicorn.Server(server_config)
    _should_stop = False
    server.run()


def start_server(host: str = "0.0.0.0", port: int = 5757) -> threading.Thread:
    global _server_thread, _should_stop

    config = config_manager.load_config()
    effective_port = config.api_port if config.api_port else port

    _server_thread = threading.Thread(
        target=_run_server,
        args=(host, effective_port),
        daemon=True,
        name="USBRelay-API",
    )
    _server_thread.start()
    logger.info(f"API server started on {host}:{effective_port}")
    return _server_thread


def stop_server():
    global _should_stop
    _should_stop = True
