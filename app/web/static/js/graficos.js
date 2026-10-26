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
                             { axisLabel: { color: C.sec, fontSize: 9, interval: 0 } }),
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
    criar("graf-rosca", {
      color: paleta,
      legend: T.legenda(C, { orient: "vertical", right: 0, top: "middle" }),
      series: [{
        type: "pie", radius: ["52%", "80%"], center: ["28%", "50%"],
        label: { show: false }, labelLine: { show: false },
        itemStyle: { borderColor: C.superficie, borderWidth: 2 },
        data: fatias.map(function (f, i) {
          return { value: f.valor_centavos, name: f.nome, fmt: f.valor_fmt,
                   itemStyle: { color: paleta[i % paleta.length] } };
        })
      }]
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
      if (d.rosca.length) { rosca(d.rosca); }
      colunas("graf-parcelas", d.parcelas, function () { return C.primaria; });
      colunas("graf-saldo", d.saldo_projetado, function (p) { return p.negativo ? C.alerta : C.primaria; });
      barras(d.maiores);
      comparativo(d.comparativo);
    })
    .catch(function () {
      aviso("Não foi possível carregar os gráficos. Os números estão na tabela de apoio.");
    });

  window.addEventListener("resize", function () {
    graficos.forEach(function (g) { g.resize(); });
  });
})();
