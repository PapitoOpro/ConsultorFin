"""Cheque especial: dias de uso sem juros no mês, estado do alerta e configuração."""
import json
import logging
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from typing import Any

from app.dominio.dinheiro import ValorMuitoAlto, verificar_magnitude
from app.repo import configuracoes, contas
from app.repo import lancamentos as repo_lanc

log = logging.getLogger(__name__)

CHAVE_CONFIG = "cheque_especial"
CHAVE_DIAS = "cheque_especial_dias"


@dataclass(frozen=True)
class ConfigChequeEspecial:
    conta_id: int
    limite_centavos: int
    dias_isentos: int = 10
    alerta_a_partir_de: int = 8


@dataclass(frozen=True)
class ChequeEspecial:
    config: ConfigChequeEspecial
    em_uso_centavos: int
    excesso_centavos: int
    dias_calculados: int
    dias_informados: int | None
    dias_usados: int
    estado: str  # ok | atencao | esgotado
    dias_restantes: int
    pct_limite: int


def estado_de(dias: int, cfg: ConfigChequeEspecial) -> str:
    if dias >= cfg.dias_isentos:
        return "esgotado"
    if dias >= cfg.alerta_a_partir_de:
        return "atencao"
    return "ok"


def validar_config(conta_id: int, limite_centavos: int, dias_isentos: int,
                   alerta_a_partir_de: int) -> ConfigChequeEspecial:
    if limite_centavos <= 0:
        raise ValueError("O limite deve ser maior que zero.")
    try:
        verificar_magnitude(limite_centavos)
    except ValorMuitoAlto as exc:
        raise ValueError("Valor muito alto.") from exc
    if not 1 <= dias_isentos <= 31:
        raise ValueError("Os dias sem juros devem ficar entre 1 e 31.")
    if not 1 <= alerta_a_partir_de <= dias_isentos:
        raise ValueError("O aviso deve começar entre 1 e o total de dias sem juros.")
    return ConfigChequeEspecial(conta_id, limite_centavos, dias_isentos, alerta_a_partir_de)


def salvar_config(con: Any, cfg: ConfigChequeEspecial) -> None:
    configuracoes.definir(con, CHAVE_CONFIG, json.dumps(asdict(cfg)))


def carregar_config(con: Any) -> ConfigChequeEspecial | None:
    bruto = configuracoes.obter(con, CHAVE_CONFIG)
    if not bruto:
        return None
    try:
        return ConfigChequeEspecial(**json.loads(bruto))
    except (ValueError, TypeError):
        log.warning("event=cheque_especial_config_invalida")
        return None


def informar_dias(con: Any, hoje: date, dias: int) -> None:
    """Dias de uso segundo o banco; vale só no mês de `hoje`."""
    if not 0 <= dias <= 31:
        raise ValueError("Os dias devem ficar entre 0 e 31.")
    configuracoes.definir(con, CHAVE_DIAS, json.dumps({"mes": f"{hoje.year:04d}-{hoje.month:02d}", "dias": dias}))


def _dias_informados(con: Any, hoje: date) -> int | None:
    bruto = configuracoes.obter(con, CHAVE_DIAS)
    if not bruto:
        return None
    try:
        d = json.loads(bruto)
        if d["mes"] == f"{hoje.year:04d}-{hoje.month:02d}":
            return int(d["dias"])
    except (ValueError, KeyError, TypeError):
        pass
    return None


def contar_dias_negativos(saldo_inicial_centavos: int, data_saldo_inicial: str | None,
                          somas: list[tuple[str, int]], primeiro_dia: date, hoje: date) -> int:
    """Dias de `primeiro_dia` a `hoje` com saldo ao fim do dia negativo (sem dias anteriores ao saldo inicial)."""
    inicio = primeiro_dia
    if data_saldo_inicial is not None:
        inicio = max(inicio, date.fromisoformat(data_saldo_inicial))
    saldo = saldo_inicial_centavos
    idx = 0
    dias = 0
    dia = inicio
    while dia <= hoje:
        iso = dia.isoformat()
        while idx < len(somas) and somas[idx][0] <= iso:
            saldo += somas[idx][1]
            idx += 1
        if saldo < 0:
            dias += 1
        dia += timedelta(days=1)
    return dias


def calcular(con: Any, hoje: date) -> ChequeEspecial | None:
    cfg = carregar_config(con)
    if cfg is None or contas.obter(con, cfg.conta_id) is None:
        return None
    inicial, data_inicial = contas.saldo_inicial_da_conta(con, cfg.conta_id)
    somas = repo_lanc.somas_diarias_compensados(con, cfg.conta_id, hoje.isoformat())
    calculados = contar_dias_negativos(inicial, data_inicial, somas, hoje.replace(day=1), hoje)
    saldo_atual = inicial + sum(v for _, v in somas)
    em_uso = max(0, -saldo_atual)
    informados = _dias_informados(con, hoje)
    usados = calculados if informados is None else informados
    estado = estado_de(usados, cfg)
    log.info("event=cheque_especial_calculado estado=%s dias=%d", estado, usados)
    return ChequeEspecial(
        config=cfg, em_uso_centavos=em_uso, excesso_centavos=max(0, em_uso - cfg.limite_centavos),
        dias_calculados=calculados, dias_informados=informados, dias_usados=usados, estado=estado,
        dias_restantes=max(0, cfg.dias_isentos - usados),
        pct_limite=max(0, min(100, em_uso * 100 // cfg.limite_centavos)))
