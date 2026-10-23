from pathlib import Path

RAIZ: Path = Path(__file__).resolve().parent.parent
DB_PATH: Path = RAIZ / "data" / "consultorfin.db"
MESES_JANELA: tuple[int, ...] = (3, 6, 12)
MESES_PADRAO: int = 6
JANELA_VENCER_DIAS: int = 7
