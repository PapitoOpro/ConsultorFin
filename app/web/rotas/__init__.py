"""Roteadores das telas."""
from fastapi import APIRouter

from app.web.rotas import importar, inicio, lancamentos

routers: list[APIRouter] = [inicio.router, importar.router, lancamentos.router]
