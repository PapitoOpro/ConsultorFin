"""Roteadores das telas."""
from fastapi import APIRouter

from app.web.rotas import importar, inicio

routers: list[APIRouter] = [inicio.router, importar.router]
