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
  var NEUTRO = C.cinza3;  // barras sem destaque; o acento marca só o que importa

  var agendado = false;
  function redimensionar() {
    if (agendado) { return; }
    agendado = true;
    window.requestAnimationFrame(function () {
      agendado = false;
      graficos.forEach(function (g) { g.resize(); });
    });
  }
  var observador = typeof ResizeObserver !== "undefined" ? new ResizeObserver(redimensionar) : null;

  function criar(id, opcoes) {
    var el = document.getElementById(id);
    if (!el) { return null; }
    var g = echarts.init(el, null, { renderer: "svg" });
    g.setOption(Object.assign(T.base(C), opcoes));
    graficos.push(g);
    if (observador) { observador.observe(el); }
    return g;
  }

  var tip = T.textoTooltip;
  var detalhes = { maiores: {}, rosca: {}, cartoes: {} };

  function el(tag, classe, texto) {
    var e = document.createElement(tag);
    if (classe) { e.className = classe; }
    if (texto !== undefined) { e.textContent = texto; }
    return e;
  }

  /* Painel com a origem do valor clicado. Só textContent: nada de HTML montado a partir de dados. */
  function mostrarDetalhe(chave, nome) {
    var painel = document.getElementById("detalhe-" + chave);
    var d = (detalhes[chave] || {})[nome];
    if (!painel || !d) { return; }
    if (!painel.hidden && painel.dataset.nome === nome) { painel.hidden = true; return; }  // 2º clique fecha
    painel.dataset.nome = nome;
    painel.textContent = "";
    var topo = el("div", "detalhe-topo");
    var tit = el("div");
    tit.appendChild(el("h3", "", d.titulo + " · " + d.total_fmt));
    tit.appendChild(el("span", "secundario", d.n + (d.n === 1 ? " item" : " itens") + " compõem este valor"));
    var fechar = el("button", "detalhe-fechar", "×");
    fechar.type = "button";
    fechar.setAttribute("aria-label", "Fechar detalhe");
    fechar.addEventListener("click", function () { painel.hidden = true; });
    topo.appendChild(tit);
    topo.appendChild(fechar);
    painel.appendChild(topo);
    if (d.nota) { painel.appendChild(el("p", "detalhe-nota", d.nota)); }
    var lista = el("ul", "detalhe-lista");
    d.linhas.forEach(function (l) {
      var li = el("li");
      li.appendChild(el("span", "desc", l.descricao));
      li.appendChild(el("span", "valor", l.valor_fmt));
      li.appendChild(el("span", "meta", [l.data, l.origem, l.conta, l.categoria].filter(Boolean).join(" · ")));
      lista.appendChild(li);
    });
    painel.appendChild(lista);
    var rodape = el("p", "detalhe-rodape");
    rodape.appendChild(el("span", "", d.restantes > 0 ? "+" + d.restantes + " itens menores não listados" : "Todos os itens listados"));
    if (d.link) {
      var a = el("a", "", d.link_rotulo || "Ver no extrato");
      a.href = d.link;
      rodape.appendChild(a);
    }
    painel.appendChild(rodape);
    painel.hidden = false;
    painel.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }

  document.addEventListener("keydown", function (e) {
    if (e.key !== "Escape") { return; }
    document.querySelectorAll(".detalhe-grafico:not([hidden])").forEach(function (p) { p.hidden = true; });
  });

  function aoClicar(g, chave) {
    if (g) {
      g.on("click", function (p) {
        mostrarDetalhe(chave, p.componentType === "yAxis" ? p.value : p.name);  // barra/fatia ou nome no eixo
      });
    }
  }

  function reais(centavos) {
    var n = Math.round(Math.abs(centavos) / 100);
    return (centavos < 0 ? "-" : "") + "R$ " + n.toLocaleString("pt-BR");
  }

  /* degradê vertical (topo cheio -> base translúcida); cores das variáveis CSS são hex #rrggbb */
  function degrade(cor, invertido) {
    var a = [{ offset: 0, color: cor }, { offset: 1, color: cor + "55" }];
    return new echarts.graphic.LinearGradient(0, invertido ? 1 : 0, 0, invertido ? 0 : 1, a);
  }

  function colunas(id, pontos, corDe) {
    var muitos = pontos.length > 6;
    return criar(id, {
      grid: T.grade({ top: 22, bottom: 20 }),
      xAxis: T.eixoCategoria(pontos.map(function (p) { return p.rotulo; }), C,
                             { axisLabel: { color: C.sec, fontSize: 10, interval: 0, hideOverlap: true } }),
      yAxis: T.eixoValor(C),
      series: [{
        type: "bar", barMaxWidth: 34, labelLayout: { hideOverlap: true },
        markLine: { silent: true, symbol: "none", label: { show: false }, lineStyle: { color: C.sec, width: 1, type: "solid" }, data: [{ yAxis: 0 }] },
        emphasis: { itemStyle: { opacity: 1 } }, select: { disabled: true },
        label: {
          show: true, color: C.sec, fontSize: 10, fontFamily: C.dados,
          position: "top", formatter: function (p) { return muitos ? compacto(p.value) : reais(p.value); }
        },
        data: pontos.map(function (p) {
          return { value: p.valor_centavos, fmt: p.valor_fmt, name: p.rotulo,
                   label: { position: p.valor_centavos < 0 ? "bottom" : "top" },
                   itemStyle: { color: degrade(corDe(p), p.valor_centavos < 0), borderRadius: p.valor_centavos < 0 ? [0, 0, 8, 8] : [8, 8, 0, 0] } };
        })
      }]
    });
  }

  /* Linha suave com área em degradê e linha do zero: evolução do saldo ao longo dos meses. */
  function area(id, pontos) {
    var ultimo = pontos.length - 1;
    var neg = pontos.some(function (p) { return p.negativo; });
    var cor = neg ? C.saida : C.primaria;
    return criar(id, {
      grid: { left: 8, right: 28, top: 28, bottom: 4, containLabel: true },
      tooltip: T.tooltip(C, {
        trigger: "axis", axisPointer: { type: "line", lineStyle: { color: C.borda } },
        formatter: function (ps) { return T.esc(ps[0].name) + ": " + T.esc(ps[0].data.fmt); }
      }),
      xAxis: T.eixoCategoria(pontos.map(function (p) { return p.rotulo; }), C, {
        boundaryGap: false, axisLine: { show: false }, axisLabel: { color: C.sec, fontSize: 11, margin: 12 }
      }),
      yAxis: T.eixoValor(C, { splitLine: { lineStyle: { color: C.borda, type: "dashed" } } }),
      series: [{
        type: "line", smooth: 0.3, smoothMonotone: "x", symbol: "circle", symbolSize: 7,
        lineStyle: { width: 3, color: cor }, itemStyle: { color: cor, borderColor: C.superficie, borderWidth: 2 },
        areaStyle: { color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
          { offset: 0, color: cor + "00" }, { offset: 1, color: cor + "55" }]) },
        markLine: { silent: true, symbol: "none", label: { show: false }, lineStyle: { color: C.sec, width: 1, type: "solid" }, data: [{ yAxis: 0 }] },
        label: {
          show: true, color: C.texto, fontSize: 11, fontFamily: C.dados, position: "top", distance: 10,
          formatter: function (p) { return p.dataIndex === 0 || p.dataIndex === ultimo ? reais(p.value) : ""; }
        },
        data: pontos.map(function (p, i) {
          var d = { value: p.valor_centavos, fmt: p.valor_fmt, name: p.rotulo };
          if (i === ultimo) { d.label = { align: "right" }; }  // não vaza pela borda direita
          else if (i === 0) { d.label = { align: "left" }; }
          return d;
        })
      }]
    });
  }

  function rosca(fatias) {
    var paleta = T.paleta(C);
    var total = fatias.reduce(function (t, f) { return t + f.valor_centavos; }, 0);
    aoClicar(criar("graf-rosca", {
      color: paleta,
      tooltip: T.tooltip(C, {
        confine: true,
        position: function () { return [0, 0]; },  // canto do contêiner: nunca cobre o total central
        formatter: function (p) {
          var pct = total > 0 ? Math.round(p.value * 1000 / total) / 10 : 0;
          return T.esc(p.name) + ": " + T.esc(p.data && p.data.fmt !== undefined ? p.data.fmt : T.moeda(p.value)) +
            " (" + pct.toLocaleString("pt-BR", { minimumFractionDigits: 1, maximumFractionDigits: 1 }) + "%)";
        }
      }),
      title: {
        text: T.moeda(total), subtext: "total em saídas", left: "center", top: "center",
        itemGap: 4,
        textStyle: { color: C.texto, fontSize: 18, fontWeight: 500, fontFamily: C.dados },
        subtextStyle: { color: C.sec, fontSize: 11, fontFamily: C.fonte }
      },
      series: [{
        type: "pie", radius: ["62%", "86%"], center: ["50%", "50%"],
        label: { show: false }, labelLine: { show: false }, avoidLabelOverlap: false,
        itemStyle: { borderColor: C.superficie, borderWidth: 2, borderRadius: 2 },
        emphasis: { scaleSize: 4 },
        data: fatias.map(function (f, i) {
          return { value: f.valor_centavos, name: f.nome, fmt: f.valor_fmt,
                   itemStyle: { color: paleta[i % paleta.length] } };
        })
      }]
    }), "rosca");
  }

  function compacto(centavos) {
    var n = Math.abs(centavos) / 100;
    var txt = n >= 1000000 ? (n / 1000000).toFixed(1) + " mi"
      : n >= 1000 ? (n / 1000).toFixed(n >= 10000 ? 0 : 1) + " mil" : String(Math.round(n));
    return "R$ " + txt.replace(".", ",");
  }

  function fluxo(meses) {
    var topo = 0;  // escala mínima de R$ 1.000 quando todos os meses são baixos/zero
    meses.forEach(function (m) { topo = Math.max(topo, m.entradas_centavos, m.saidas_centavos); });
    function serie(nome, chave, fmt, cor) {
      var ultimo = meses.length - 1;  // só o mês atual ganha ponto
      return {
        name: nome, type: "line", smooth: 0.35, smoothMonotone: "x", symbol: "none",
        lineStyle: { width: 2, color: cor }, itemStyle: { color: cor, borderColor: C.superficie, borderWidth: 2 },
        data: meses.map(function (m, i) {
          var d = { value: m[chave], fmt: m[fmt], name: m.rotulo };
          if (i === ultimo) { d.symbol = "circle"; d.symbolSize = 8; }
          return d;
        })
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
        show: true, min: 0, max: topo < 100000 ? 100000 : undefined,
        axisLabel: { color: C.sec, fontSize: 10, fontFamily: C.dados, formatter: compacto },
        splitLine: { lineStyle: { color: C.borda } }
      }),
      series: [serie("Entradas", "entradas_centavos", "entradas_fmt", C.primaria),
               serie("Saídas", "saidas_centavos", "saidas_fmt", C.saida)]
    });
  }

  function larguraRotulo(id) {
    var el = document.getElementById(id);
    return Math.max(70, Math.min(150, Math.round((el ? el.clientWidth : 300) * 0.32)));
  }

  function barras(id, chave, itens, cor, rotuloExtra) {
    var ord = itens.slice().reverse();  // maior no topo
    aoClicar(criar(id, {
      grid: { left: 4, right: 96, top: 4, bottom: 4, containLabel: true },
      xAxis: { type: "value", show: false },
      yAxis: {
        type: "category", data: ord.map(function (i) { return i.nome; }),
        axisLine: { show: false }, axisTick: { show: false },
        axisLabel: { color: C.sec, fontSize: 11, width: larguraRotulo(id), overflow: "break" }, triggerEvent: true
      },
      series: [{
        type: "bar", barMaxWidth: 16, showBackground: true,
        backgroundStyle: { color: C.superficie2, borderRadius: 8 },
        label: {
          show: true, position: "right", color: C.sec, fontSize: 11, fontFamily: C.dados,
          formatter: function (p) { return p.data.fmt + (rotuloExtra ? "  " + p.data[rotuloExtra] : ""); }
        },
        data: ord.map(function (i, idx) {
          var lider = idx === ord.length - 1;  // maior item (último após inverter) leva o acento
          return { value: i.valor_centavos, fmt: i.valor_fmt, pct: i.percentual, name: i.nome,
                   itemStyle: { color: lider ? C.primaria : cor, borderRadius: 8 } };
        })
      }]
    }), chave);
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
      detalhes = d.detalhes || detalhes;
      fluxo(d.fluxo);
      if (d.rosca.length) { rosca(d.rosca); }
      aoClicar(colunas("graf-parcelas", d.parcelas, function () { return C.info; }), "parcelas");
      aoClicar(area("graf-saldo", d.saldo_projetado), "saldo");
      if (d.maiores.length) { barras("graf-maiores", "maiores", d.maiores, NEUTRO, "pct"); }
      if (d.cartoes.length) { barras("graf-cartoes", "cartoes", d.cartoes, NEUTRO); }
    })
    .catch(function () {
      aviso("Não foi possível carregar os gráficos. Os números estão na tabela de apoio.");
    });

  window.addEventListener("resize", redimensionar);
  window.addEventListener("pagehide", function () {
    if (observador) { observador.disconnect(); }
    graficos.forEach(function (g) { g.dispose(); });
    graficos.length = 0;
  });
})();
