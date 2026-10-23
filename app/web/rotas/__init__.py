"""Roteadores das telas."""
from fastapi import APIRouter

from app.web.rotas import inicio

routers: list[APIRouter] = [inicio.router]
