import sqlite3
from collections.abc import Iterator

import pytest

from app.db import conectar, migrar


@pytest.fixture
def con() -> Iterator[sqlite3.Connection]:
    c = conectar(":memory:")
    migrar(c)
    yield c
    c.close()


@pytest.fixture(autouse=True)
def _chave_senhas_isolada(tmp_path_factory: pytest.TempPathFactory,
                          monkeypatch: pytest.MonkeyPatch) -> None:
    """Nenhum teste toca em data/senhas.key."""
    monkeypatch.setenv("CONSULTORFIN_SENHAS_KEY",
                       str(tmp_path_factory.mktemp("chave") / "senhas.key"))
