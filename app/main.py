"""Aplicação web ConsultorFin.

Execução (fábrica preguiçosa; importar este módulo não cria nem toca `data/`):
    uvicorn app.main:create_app --factory --port 8000
"""
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date, datetime
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import config
from app.chat.estado import EstadoChat
from app.chat.processo import executar_claude
from app.chat.verificacao import chat_pode_iniciar
from app.db import conectar, migrar
from app.dominio.contas_pagar import contar_vencidas
from app.dominio.dinheiro import fim_do_mes_iso, formatar_brl, iso_para_br
from app.relatorio.montar import limpar_expirados
from app.web.rotas import router_chat, routers
from app.web.seguranca import ProtecaoOrigemHost
from app.web.uploads import ArmazemFaturas, ArmazemHolerites, ArmazemUploads

WEB_DIR: Path = Path(__file__).resolve().parent / "web"


log = logging.getLogger(__name__)


def create_app(caminho_db: Path | None = None) -> FastAPI:
    destino = config.DB_PATH if caminho_db is None else caminho_db

    @asynccontextmanager
    async def lifespan(app_: FastAPI) -> AsyncIterator[None]:
        con = conectar(destino)
        try:
            migrar(con)
        finally:
            con.close()
        limpar_expirados(app_.state.dir_relatorios, datetime.now())
        yield

    app = FastAPI(title="ConsultorFin", lifespan=lifespan)
    app.state.caminho_db = destino
    app.state.dir_relatorios = config.DIR_RELATORIOS  # testes sobrescrevem
    def contexto_menu(request: Request) -> dict[str, int]:
        """Vencidas para o selo do menu; qualquer falha vira 0 (o menu nunca derruba a página)."""
        if request.headers.get("HX-Request"):  # fragmentos HTMX não renderizam o menu
            return {"n_vencidas": 0}
        try:
            con = conectar(request.app.state.caminho_db)
            try:
                return {"n_vencidas": contar_vencidas(con, request.app.state.hoje_fn())}
            finally:
                con.close()
        except Exception as exc:  # noqa: BLE001
            log.error("event=n_vencidas_falhou erro=%s", type(exc).__name__)
            return {"n_vencidas": 0}

    templates = Jinja2Templates(directory=str(WEB_DIR / "templates"), context_processors=[contexto_menu])
    templates.env.filters["brl"] = formatar_brl
    templates.env.filters["data_br"] = iso_para_br
    templates.env.filters["fim_do_mes"] = fim_do_mes_iso
    estaticos = (WEB_DIR / "static").rglob("*")
    templates.env.globals["versao_static"] = max((int(f.stat().st_mtime) for f in estaticos if f.is_file()), default=0)
    templates.env.globals["url_terminal"] = config.url_terminal()
    app.add_middleware(ProtecaoOrigemHost)
    app.state.templates = templates
    app.state.uploads = ArmazemUploads()
    app.state.faturas = ArmazemFaturas()
    app.state.holerites = ArmazemHolerites()
    app.state.hoje_fn = date.today  # relógio injetável (testes)
    app.mount("/static", StaticFiles(directory=str(WEB_DIR / "static")), name="static")
    app.state.chat = EstadoChat()
    app.state.claude_fn = executar_claude  # testes trocam por um gerador falso
    for r in routers:
        app.include_router(r)
    chat_registrado: bool = config.chat_ativo() and chat_pode_iniciar()  # sem porteiro, sem chat
    if chat_registrado:
        app.include_router(router_chat)
    templates.env.globals["chat_ativo"] = chat_registrado  # só se as rotas existem de fato
    return app
