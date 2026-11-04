"""Roteadores das telas."""
from fastapi import APIRouter

from app.web.rotas import (categorias, chat, contas_pagar, faturas, fila, holerites, importar, inicio, lancamentos,
                           relatorio, senhas)

routers: list[APIRouter] = [inicio.router, importar.router, faturas.router, holerites.router,
                          lancamentos.router, fila.router, senhas.router,
                          contas_pagar.router, categorias.router, relatorio.router]
router_chat: APIRouter = chat.router  # só registrado com CONSULTORFIN_CHAT=1
