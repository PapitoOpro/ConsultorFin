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
