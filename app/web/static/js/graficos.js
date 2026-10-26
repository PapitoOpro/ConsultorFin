/* Gráficos da tela Início. Só renderiza: dados vêm de /inicio/dados.json (valores já
   formatados no servidor). Cores lidas das variáveis CSS; nada de HTML montado a partir de
   texto de usuário (tooltips usam escape). */
(function () {
  "use strict";

  var raiz = document.getElementById("inicio");
  if (!raiz || typeof echarts === "undefined") { return; }

  var T = window.ConsultorFinTema;
  if (!T) { return; }
  var C = T.cores();
  var graficos = [];

  function criar(id, opcoes) {
    var el = document.getElementById(id);
    if (!el) { return; }
    var g = echarts.init(el, null, { renderer: "svg" });
    g.setOption(Object.assign(T.base(C), opcoes));
    graficos.push(g);
  }

  var tip = T.textoTooltip;

  function colunas(id, pontos, corDe) {
    criar(id, {
      grid: T.grade(),
      xAxis: T.eixoCategoria(pontos.map(function (p) { return p.rotulo; }), C,
                             { axisLabel: { color: C.sec, fontSize: 9, interval: pontos.length > 6 ? 1 : 0, hideOverlap: true } }),
      yAxis: T.eixoValor(C),
      series: [{
        type: "bar", barMaxWidth: 18,
        data: pontos.map(function (p) {
          return { value: p.valor_centavos, fmt: p.valor_fmt, name: p.rotulo,
                   itemStyle: { color: corDe(p), borderRadius: [2, 2, 0, 0] } };
        })
      }]
    });
  }

  function rosca(fatias) {
    var paleta = T.paleta(C);
    var total = fatias.reduce(function (t, f) { return t + f.valor_centavos; }, 0);
    criar("graf-rosca", {
      color: paleta,
      title: {
        text: T.moeda(total), subtext: "total em saídas", left: "center", top: "center",
        itemGap: 4,
        textStyle: { color: C.texto, fontSize: 18, fontWeight: 700, fontFamily: C.fonte },
        subtextStyle: { color: C.sec, fontSize: 11, fontFamily: C.fonte }
      },
      series: [{
        type: "pie", radius: ["62%", "86%"], center: ["50%", "50%"],
        label: { show: false }, labelLine: { show: false }, avoidLabelOverlap: false,
        itemStyle: { borderColor: C.superficie, borderWidth: 3, borderRadius: 4 },
        emphasis: { scaleSize: 4 },
        data: fatias.map(function (f, i) {
          return { value: f.valor_centavos, name: f.nome, fmt: f.valor_fmt,
                   itemStyle: { color: paleta[i % paleta.length] } };
        })
      }]
    });
  }

  function compacto(centavos) {
    var n = Math.abs(centavos) / 100;
    var txt = n >= 1000000 ? (n / 1000000).toFixed(1) + " mi"
      : n >= 1000 ? (n / 1000).toFixed(n >= 10000 ? 0 : 1) + " mil" : String(Math.round(n));
    return "R$ " + txt.replace(".", ",");
  }

  function fluxo(meses) {
    function serie(nome, chave, fmt, cor) {
      return {
        name: nome, type: "line", smooth: 0.35, symbol: "circle", symbolSize: 8, showSymbol: false,
        lineStyle: { width: 3, color: cor }, itemStyle: { color: cor, borderColor: C.superficie, borderWidth: 2 },
        emphasis: { focus: "series" },
        areaStyle: {
          color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: cor + "55" }, { offset: 1, color: cor + "00" }])
        },
        data: meses.map(function (m) { return { value: m[chave], fmt: m[fmt], name: m.rotulo }; })
      };
    }
    criar("graf-fluxo", {
      grid: { left: 4, right: 26, top: 36, bottom: 4, containLabel: true },
      legend: T.legenda(C, { right: 0, top: 0 }),
      tooltip: T.tooltip(C, {
        trigger: "axis", axisPointer: { type: "line", lineStyle: { color: C.borda } },
        formatter: function (ps) {
          var linhas = [T.esc(ps[0].name)];
          ps.forEach(function (p) {
            linhas.push(T.esc(p.seriesName) + ": " + T.esc(p.data.fmt));
          });
          return linhas.join("<br>");
        }
      }),
      xAxis: T.eixoCategoria(meses.map(function (m) { return m.rotulo; }), C, {
        boundaryGap: false, axisLabel: { color: C.sec, fontSize: 11, margin: 12 }
      }),
      yAxis: T.eixoValor(C, {
        show: true, min: 0, axisLabel: { color: C.aux, fontSize: 10, formatter: compacto },
        splitLine: { lineStyle: { color: C.borda, type: "dashed", opacity: .6 } }
      }),
      series: [serie("Entradas", "entradas_centavos", "entradas_fmt", C.primaria),
               serie("Saídas", "saidas_centavos", "saidas_fmt", C.saida)]
    });
  }

  function barras(itens) {
    var ord = itens.slice().reverse();  // maior no topo
    criar("graf-maiores", {
      grid: { left: 4, right: 8, top: 4, bottom: 4, containLabel: true },
      xAxis: { type: "value", show: false },
      yAxis: {
        type: "category", data: ord.map(function (i) { return i.nome; }),
        axisLine: { show: false }, axisTick: { show: false },
        axisLabel: { color: C.sec, fontSize: 10, width: 70, overflow: "truncate" }
      },
      series: [{
        type: "bar", barMaxWidth: 8,
        data: ord.map(function (i) {
          return { value: i.valor_centavos, fmt: i.valor_fmt, name: i.nome,
                   itemStyle: { color: C.primaria, borderRadius: [0, 2, 2, 0] } };
        })
      }]
    });
  }

  function comparativo(meses) {
    function serie(nome, chave, fmt, cor) {
      return {
        name: nome, type: "bar", barMaxWidth: 22, itemStyle: { color: cor },
        data: meses.map(function (m) {
          return { value: m[chave], fmt: m[fmt], name: m.rotulo + " · " + nome,
                   itemStyle: { color: cor, borderRadius: [2, 2, 0, 0] } };
        })
      };
    }
    criar("graf-comparativo", {
      grid: T.grade(),
      legend: T.legenda(C, { right: 0, top: 0 }),
      xAxis: T.eixoCategoria(meses.map(function (m) { return m.rotulo; }), C),
      yAxis: T.eixoValor(C),
      series: [serie("Entradas", "entradas_centavos", "entradas_fmt", C.entrada),
               serie("Saídas", "saidas_centavos", "saidas_fmt", C.saida)]
    });
  }

  function aviso(texto) {
    var el = document.getElementById("msg-graficos");
    if (el) { el.textContent = texto; }
  }

  var q = new URLSearchParams({ mes: raiz.dataset.mes, horizonte: raiz.dataset.horizonte });
  fetch("/inicio/dados.json?" + q.toString(), { headers: { Accept: "application/json" } })
    .then(function (r) {
      if (!r.ok) { throw new Error("status " + r.status); }
      return r.json();
    })
    .then(function (d) {
      fluxo(d.fluxo);
      if (d.rosca.length) { rosca(d.rosca); }
      colunas("graf-parcelas", d.parcelas, function () { return C.primaria; });
      colunas("graf-saldo", d.saldo_projetado, function (p) { return p.negativo ? C.alerta : C.primaria; });
      if (d.maiores.length) { barras(d.maiores); }
      comparativo(d.comparativo);
    })
    .catch(function () {
      aviso("Não foi possível carregar os gráficos. Os números estão na tabela de apoio.");
    });

  window.addEventListener("resize", function () {
    graficos.forEach(function (g) { g.resize(); });
  });
})();
