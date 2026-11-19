/* Gráficos da tela Investimentos: só renderizam dados já calculados no servidor
   (data-meses e data-dias). Sem HTML montado a partir de dados: tooltips usam escape. */
(function () {
  "use strict";
  var T = window.ConsultorFinTema;
  if (!T || typeof echarts === "undefined") { return; }
  var C = T.cores();
  var graficos = [];

  function dados(el, atributo) {
    try { return JSON.parse(el.getAttribute(atributo) || "[]"); } catch (e) { return []; }
  }

  function criar(el, opcoes) {
    var g = echarts.init(el, null, { renderer: "svg" });
    g.setOption(Object.assign(T.base(C), opcoes));
    graficos.push(g);
    return g;
  }

  /* "#rrggbb" -> "rgba(r,g,b,a)": o verde/vermelho fraco é o mesmo tom com menos opacidade */
  function rgba(hex, a) {
    var h = String(hex).replace("#", "");
    if (h.length !== 6) { return hex; }
    return "rgba(" + parseInt(h.slice(0, 2), 16) + "," + parseInt(h.slice(2, 4), 16) + "," +
      parseInt(h.slice(4, 6), 16) + "," + a + ")";
  }

  function dataBr(iso) { return iso.slice(8, 10) + "/" + iso.slice(5, 7) + "/" + iso.slice(0, 4); }

  /* ---- evolução mês a mês ---- */
  var elMeses = document.getElementById("inv-grafico");
  if (elMeses) {
    var meses = dados(elMeses, "data-meses");
    if (meses.length) {
      var serie = function (nome, campo, cor) {
        return { name: nome, type: "line", showSymbol: meses.length < 3, smooth: false,
                 lineStyle: { color: cor, width: 2 }, itemStyle: { color: cor },
                 data: meses.map(function (m) { return m[campo + "_centavos"]; }) };
      };
      criar(elMeses, {
        tooltip: { trigger: "axis", valueFormatter: T.moeda },
        legend: { textStyle: { color: C.sec }, top: 0 },
        xAxis: { type: "category", data: meses.map(function (m) { return m.mes.slice(5) + "/" + m.mes.slice(0, 4); }),
                 axisLabel: { color: C.sec } },
        yAxis: { type: "value", axisLabel: { color: C.sec, formatter: function (v) { return T.moeda(v); } },
                 splitLine: { lineStyle: { color: C.borda } } },
        series: [serie("Aportes acumulados", "aportes_liquidos_acum", C.cinza3),
                 serie("Aplicado em ativos (custo)", "aplicado_acum", C.info),
                 serie("Resultado acumulado", "resultado_acum", C.primaria)]
      });
    }
  }

  /* ---- calendário do day-trade: um quadrado por dia, verde ganhou, vermelho perdeu ---- */
  var elCal = document.getElementById("inv-calendario");
  if (elCal) {
    var dias = dados(elCal, "data-dias");
    if (dias.length) {
      var ultimo = dias[dias.length - 1].data;
      var fim = new Date(Number(ultimo.slice(0, 4)), Number(ultimo.slice(5, 7)), 0);   // último dia do mês
      var ini = new Date(Number(ultimo.slice(0, 4)), Number(ultimo.slice(5, 7)) - 1 - 5, 1);  // até 6 meses
      var pad = function (n) { return (n < 10 ? "0" : "") + n; };
      var iso = function (d) { return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate()); };
      var inicioIso = iso(ini);
      var visiveis = dias.filter(function (d) { return d.data >= inicioIso; });
      var m = Math.max.apply(null, visiveis.map(function (d) { return Math.abs(d.valor_centavos); })) || 1;
      var celula = 22;
      elCal.style.height = (7 * celula + 62) + "px";
      criar(elCal, {
        tooltip: {
          formatter: function (p) {
            var v = p.value[1];
            return T.esc(dataBr(p.value[0])) + "<br>" + (v > 0 ? "Ganho " : v < 0 ? "Perda " : "Zerado ") + T.esc(T.moeda(Math.abs(v)));
          }
        },
        visualMap: {
          type: "piecewise", show: false, dimension: 1, seriesIndex: 0,
          pieces: [
            { lte: -m / 2, color: C.saida },
            { gt: -m / 2, lt: 0, color: rgba(C.saida, 0.5) },
            { value: 0, color: C.cinza3 },
            { gt: 0, lt: m / 2, color: rgba(C.primaria, 0.5) },
            { gte: m / 2, color: C.primaria }
          ]
        },
        calendar: {
          range: [inicioIso, iso(fim)], cellSize: [celula, celula], top: 36, left: 34, right: 8,
          orient: "horizontal", yearLabel: { show: false },
          splitLine: { show: false },
          itemStyle: { color: C.superficie2, borderColor: C.superficie, borderWidth: 3 },
          dayLabel: { firstDay: 0, nameMap: ["D", "S", "T", "Q", "Q", "S", "S"], color: C.sec, margin: 8 },
          monthLabel: { nameMap: ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"],
                        color: C.sec, margin: 10 }
        },
        series: [{
          type: "heatmap", coordinateSystem: "calendar",
          data: visiveis.map(function (d) { return [d.data, d.valor_centavos]; }),
          itemStyle: { borderRadius: 4, borderColor: C.superficie, borderWidth: 3 },
          emphasis: { itemStyle: { borderColor: C.texto, borderWidth: 2 } }
        }]
      });
    }
  }

  var agendado = false;
  window.addEventListener("resize", function () {
    if (agendado) { return; }
    agendado = true;
    window.requestAnimationFrame(function () {
      agendado = false;
      graficos.forEach(function (g) { g.resize(); });
    });
  });
})();
