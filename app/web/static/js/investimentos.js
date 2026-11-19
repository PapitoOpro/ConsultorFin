/* Gráfico da tela Investimentos: só renderiza dados já calculados no servidor (data-meses). */
(function () {
  "use strict";
  var el = document.getElementById("inv-grafico");
  var T = window.ConsultorFinTema;
  if (!el || !T || typeof echarts === "undefined") { return; }
  var meses;
  try { meses = JSON.parse(el.getAttribute("data-meses") || "[]"); } catch (e) { return; }
  if (!meses.length) { return; }
  var C = T.cores();
  var g = echarts.init(el, null, { renderer: "svg" });
  function serie(nome, campo, cor) {
    return { name: nome, type: "line", showSymbol: meses.length < 3, smooth: false,
             lineStyle: { color: cor, width: 2 }, itemStyle: { color: cor },
             data: meses.map(function (m) { return m[campo + "_centavos"]; }) };
  }
  g.setOption(Object.assign(T.base(C), {
    tooltip: { trigger: "axis", valueFormatter: T.moeda },
    legend: { textStyle: { color: C.sec }, top: 0 },
    xAxis: { type: "category", data: meses.map(function (m) { return m.mes.slice(5) + "/" + m.mes.slice(0, 4); }),
             axisLabel: { color: C.sec } },
    yAxis: { type: "value", axisLabel: { color: C.sec, formatter: function (v) { return T.moeda(v); } },
             splitLine: { lineStyle: { color: C.borda } } },
    series: [serie("Aportes acumulados", "aportes_liquidos_acum", C.cinza3),
             serie("Aplicado em ativos (custo)", "aplicado_acum", C.info),
             serie("Resultado acumulado", "resultado_acum", C.primaria)]
  }));
  window.addEventListener("resize", function () { g.resize(); });
})();
