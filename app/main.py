"""Aplicação web ConsultorFin.

Execução (fábrica preguiçosa; importar este módulo não cria nem toca `data/`):
    uvicorn app.main:create_app --factory --port 8000
"""
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import config
from app.db import conectar, migrar
from app.dominio.dinheiro import fim_do_mes_iso, formatar_brl, iso_para_br
from app.web.rotas import routers
from app.web.seguranca import ProtecaoOrigemHost
from app.web.uploads import ArmazemFaturas, ArmazemHolerites, ArmazemUploads

WEB_DIR: Path = Path(__file__).resolve().parent / "web"


def create_app(caminho_db: Path | None = None) -> FastAPI:
    destino = config.DB_PATH if caminho_db is None else caminho_db

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        con = conectar(destino)
        try:
            migrar(con)
        finally:
            con.close()
        yield

    app = FastAPI(title="ConsultorFin", lifespan=lifespan)
    app.state.caminho_db = destino
    templates = Jinja2Templates(directory=str(WEB_DIR / "templates"))
    templates.env.filters["brl"] = formatar_brl
    templates.env.filters["data_br"] = iso_para_br
    templates.env.filters["fim_do_mes"] = fim_do_mes_iso
    app.add_middleware(ProtecaoOrigemHost)
    app.state.templates = templates
    app.state.uploads = ArmazemUploads()
    app.state.faturas = ArmazemFaturas()
    app.state.holerites = ArmazemHolerites()
    app.state.hoje_fn = date.today  # relógio injetável (testes)
    app.mount("/static", StaticFiles(directory=str(WEB_DIR / "static")), name="static")
    for r in routers:
        app.include_router(r)
    return app
