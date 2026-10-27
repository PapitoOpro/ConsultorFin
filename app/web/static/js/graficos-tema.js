/* Tema ECharts do ConsultorFin: só monta fragmentos de `option` (cores lidas das variáveis CSS).
   Não depende de echarts nem toca o DOM; texto de tooltip sempre escapado (nunca HTML montado a partir de dados). */
(function (janela) {
  "use strict";

  var NOMES = {
    fundo: "--fundo", superficie: "--superficie", superficie2: "--superficie-2", borda: "--borda",
    texto: "--texto", sec: "--texto-secundario", aux: "--texto-auxiliar",
    primaria: "--primaria", entrada: "--entrada", saida: "--saida", alerta: "--alerta",
    info: "--info", roxo: "--roxo", fonte: "--fonte-texto"
  };

  function cores() {
    var estilo = getComputedStyle(document.documentElement);
    var c = {};
    Object.keys(NOMES).forEach(function (k) { c[k] = estilo.getPropertyValue(NOMES[k]).trim(); });
    return c;
  }

  function paleta(c) {
    c = c || cores();
    return [c.primaria, c.info, c.roxo, c.alerta, c.saida, c.sec];
  }

  function esc(t) {
    return String(t).replace(/[&<>"']/g, function (ch) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch];
    });
  }

  /* centavos (inteiro) -> "R$ 1.234,56" */
  function moeda(centavos) {
    var n = Number(centavos) / 100;
    if (!isFinite(n)) { return ""; }
    return n.toLocaleString("pt-BR", { style: "currency", currency: "BRL" });
  }

  function reduzMovimento() {
    return !!(janela.matchMedia && janela.matchMedia("(prefers-reduced-motion: reduce)").matches);
  }

  /* Texto do tooltip: usa o valor já formatado no servidor (data.fmt) ou formata centavos. */
  function textoTooltip(p) {
    var d = p && p.data ? p.data : {};
    var valor = d.fmt !== undefined ? d.fmt : moeda(p.value);
    return esc(p.name) + ": " + esc(valor);
  }

  function tooltip(c, extra) {
    c = c || cores();
    return Object.assign({
      trigger: "item", backgroundColor: c.superficie, borderColor: c.borda, borderWidth: 1,
      padding: [6, 10], extraCssText: "border-radius:10px;box-shadow:0 8px 24px rgba(0,0,0,.35);",
      textStyle: { color: c.texto, fontSize: 12, fontFamily: c.fonte },
      formatter: textoTooltip
    }, extra || {});
  }

  function legenda(c, extra) {
    c = c || cores();
    return Object.assign({
      icon: "circle", itemWidth: 8, itemHeight: 8, itemGap: 14,
      textStyle: { color: c.sec, fontSize: 11, fontFamily: c.fonte }
    }, extra || {});
  }

  function grade(extra) {
    return Object.assign({ left: 4, right: 4, top: 8, bottom: 18 }, extra || {});
  }

  function eixoCategoria(rotulos, c, extra) {
    c = c || cores();
    return Object.assign({
      type: "category", data: rotulos,
      axisLine: { lineStyle: { color: c.borda } }, axisTick: { show: false },
      axisLabel: { color: c.sec, fontSize: 10, fontFamily: c.fonte }
    }, extra || {});
  }

  function eixoValor(c, extra) {
    c = c || cores();
    return Object.assign({
      type: "value", show: false,
      splitLine: { lineStyle: { color: c.borda, type: "dashed" } }
    }, extra || {});
  }

  /* Opções comuns a todo gráfico: fundo transparente, fonte, paleta, animação respeitando o SO. */
  function base(c, extra) {
    c = c || cores();
    var anima = !reduzMovimento();
    return Object.assign({
      backgroundColor: "transparent",
      color: paleta(c),
      animation: anima, animationDuration: anima ? 400 : 0, animationDurationUpdate: anima ? 300 : 0,
      textStyle: { color: c.sec, fontFamily: c.fonte, fontSize: 11 },
      tooltip: tooltip(c)
    }, extra || {});
  }

  window.ConsultorFinTema = {
    cores: cores, paleta: paleta, esc: esc, moeda: moeda, reduzMovimento: reduzMovimento,
    textoTooltip: textoTooltip, tooltip: tooltip, legenda: legenda, grade: grade,
    eixoCategoria: eixoCategoria, eixoValor: eixoValor, base: base
  };
})(window);
