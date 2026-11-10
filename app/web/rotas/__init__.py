"""Roteadores das telas."""
from fastapi import APIRouter

from app.web.rotas import (cadastros, categorias, chat, cheque_especial, faturas, fila, holerites, importar, inicio, lancamentos,
                           relatorio, senhas)

routers: list[APIRouter] = [inicio.router, importar.router, faturas.router, holerites.router,
                          lancamentos.router, fila.router, senhas.router,
                          cadastros.router, categorias.router, relatorio.router, cheque_especial.router]
router_chat: APIRouter = chat.router  # só registrado com CONSULTORFIN_CHAT=1
