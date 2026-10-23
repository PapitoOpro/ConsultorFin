"""Tela Início (placeholder; tarefas seguintes preenchem)."""
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def inicio(request: Request) -> HTMLResponse:
    return request.app.state.templates.TemplateResponse(request, "inicio.html", {"ativo": "inicio"})
