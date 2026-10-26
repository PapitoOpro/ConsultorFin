"""Roteadores das telas."""
from fastapi import APIRouter

from app.web.rotas import categorias, contas_pagar, importar, inicio, lancamentos

routers: list[APIRouter] = [inicio.router, importar.router, lancamentos.router,
                          contas_pagar.router, categorias.router]
