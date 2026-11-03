import os
from pathlib import Path

RAIZ: Path = Path(__file__).resolve().parent.parent
DB_PATH: Path = RAIZ / "data" / "consultorfin.db"
MESES_JANELA: tuple[int, ...] = (3, 6, 12)
MESES_PADRAO: int = 6
JANELA_VENCER_DIAS: int = 7
# Hosts extras aceitos pelo middleware de segurança (env CONSULTORFIN_HOSTS, separados por vírgula).
HOSTS_PERMITIDOS: tuple[str, ...] = tuple(
    h.strip().lower() for h in os.environ.get("CONSULTORFIN_HOSTS", "").split(",") if h.strip()
)


def url_terminal() -> str:
    """URL https do terminal do consultor (env CONSULTORFIN_TERMINAL_URL); vazio desativa o balão."""
    url = os.environ.get("CONSULTORFIN_TERMINAL_URL", "").strip()
    return url if url.lower().startswith("https://") else ""


def caminho_token_gmail() -> Path:
    """JSON do token OAuth do Gmail (env CONSULTORFIN_GMAIL_TOKEN; padrão data/gmail-token.json)."""
    valor = os.environ.get("CONSULTORFIN_GMAIL_TOKEN", "").strip()
    return Path(valor) if valor else RAIZ / "data" / "gmail-token.json"


DIR_FILA: Path = RAIZ / "data" / "fila"
LIMITE_MENSAGENS_GMAIL: int = 50
DIAS_EXPIRACAO_FILA: int = 7
LIMITE_PDF_BYTES: int = 5 * 1024 * 1024
