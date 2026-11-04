/* Página /relatorio/<id>. Lê o retrato (JSON) do <script type="application/json"> e monta gráficos
   e tabela. Todo texto vem de dados não confiáveis: DOM só com createElement/textContent;
   tooltips passam por ConsultorFinTema.esc. Sem degradês. */
(function () {
  "use strict";

  var T = window.ConsultorFinTema;
  var fonte = document.getElementById("dados-relatorio");
  if (!T || !fonte) { return; }
  var dados;
  try { dados = JSON.parse(fonte.textContent); } catch (e) { return; }
  var C = T.cores();
  var graficos = [];

  function el(tag, classe, texto) {
    var e = document.createElement(tag);
    if (classe) { e.className = classe; }
    if (texto !== undefined && texto !== null) { e.textContent = texto; }
    return e;
  }

  function formatar(valor, formato) {
    if (typeof valor !== "number" || !isFinite(valor)) { return "—"; }
    if (formato === "moeda") { return T.moeda(valor); }
    if (formato === "pct") { return valor.toLocaleString("pt-BR", { maximumFractionDigits: 1 }) + "%"; }
    return valor.toLocaleString("pt-BR");
  }

  function compacto(valor, formato) {
    if (formato !== "moeda") { return formatar(valor, formato); }
    var n = Math.abs(valor) / 100;
    var t = n >= 1000000 ? (n / 1000000).toFixed(1) + " mi" : n >= 1000 ? (n / 1000).toFixed(1) + " mil" : String(Math.round(n));
    return (valor < 0 ? "-" : "") + "R$ " + t.replace(".", ",");
  }

  /* ---------- gráficos ---------- */
  function tooltipItem(formato) {
    return T.tooltip(C, {
      confine: true,
      formatter: function (p) { return T.esc(p.name) + ": " + T.esc(formatar(p.value, formato)); }
    });
  }

  function tooltipEixo(formato) {
    return T.tooltip(C, {
      trigger: "axis", confine: true, axisPointer: { type: "line", lineStyle: { color: C.borda } },
      formatter: function (ps) {
        var linhas = [T.esc(ps[0].name)];
        ps.forEach(function (p) {
          var rot = p.seriesName ? T.esc(p.seriesName) + ": " : "";
          linhas.push(rot + T.esc(formatar(p.value, formato)));
        });
        return linhas.join("<br>");
      }
    });
  }

  /* agrupa as linhas em categorias (nome) x séries; sem série vira uma só, sem nome */
  function organizar(linhas) {
    var nomes = [];
    var series = [];
    var mapa = {};
    linhas.forEach(function (l) {
      var s = l.serie === null || l.serie === undefined ? "" : String(l.serie);
      if (nomes.indexOf(l.nome) < 0) { nomes.push(l.nome); }
      if (series.indexOf(s) < 0) { series.push(s); }
      mapa[s + "\u0000" + l.nome] = l.valor;
    });
    return {
      nomes: nomes, series: series,
      valor: function (s, n) {
        var v = mapa[s + "\u0000" + n];
        return v === undefined ? null : v;
      }
    };
  }

  function opcaoGrafico(g) {
    var o = organizar(g.linhas);
    var paleta = T.paleta(C);
    var multi = o.series.length > 1;
    var base = { aria: { enabled: true } };
    if (g.tipo === "rosca") {
      return Object.assign(base, {
        tooltip: tooltipItem(g.formato),
        legend: T.legenda(C, { bottom: 0, type: "scroll" }),
        series: [{
          type: "pie", radius: ["50%", "74%"], center: ["50%", "44%"],
          label: { show: false }, labelLine: { show: false },
          itemStyle: { borderColor: C.superficie, borderWidth: 2 },
          data: g.linhas.map(function (l, i) {
            return { name: l.nome, value: l.valor, itemStyle: { color: paleta[i % paleta.length] } };
          })
        }]
      });
    }
    var horizontal = g.tipo === "barras_h";
    var categorias = horizontal ? o.nomes.slice().reverse() : o.nomes;
    var eixoCat = T.eixoCategoria(categorias, C, {
      axisLabel: { color: C.sec, fontSize: 11, interval: 0, hideOverlap: true, width: horizontal ? 130 : undefined, overflow: "truncate" }
    });
    var eixoVal = T.eixoValor(C, {
      show: true,
      axisLabel: { color: C.sec, fontSize: 10, fontFamily: C.dados, formatter: function (v) { return compacto(v, g.formato); } }
    });
    var series = o.series.map(function (s, i) {
      var cor = multi ? paleta[i % paleta.length] : (g.tipo === "linha" ? C.primaria : C.cinza3);
      var valores = categorias.map(function (n) { return o.valor(s, n); });
      var item = { name: s, data: valores, itemStyle: { color: cor } };
      if (g.tipo === "linha") {
        return Object.assign(item, { type: "line", smooth: false, symbol: "circle", symbolSize: 6, lineStyle: { width: 2, color: cor } });
      }
      return Object.assign(item, { type: "bar", barMaxWidth: 28 });
    });
    if (!multi && g.tipo !== "linha" && series[0]) {
      /* o maior item leva o acento; o resto fica em cinza neutro */
      var max = -Infinity;
      var idx = -1;
      series[0].data.forEach(function (v, i) { if (v !== null && v > max) { max = v; idx = i; } });
      series[0].data = series[0].data.map(function (v, i) {
        return { value: v, itemStyle: { color: i === idx ? C.primaria : C.cinza3 } };
      });
    }
    return Object.assign(base, {
      tooltip: tooltipEixo(g.formato),
      legend: multi ? T.legenda(C, { top: 0, type: "scroll" }) : undefined,
      grid: { left: 8, right: 16, top: multi ? 32 : 12, bottom: 8, containLabel: true },
      xAxis: horizontal ? eixoVal : Object.assign(eixoCat, { boundaryGap: g.tipo !== "linha" }),
      yAxis: horizontal ? eixoCat : eixoVal,
      series: series
    });
  }

  function montarGraficos() {
    var raiz = document.getElementById("relatorio-graficos");
    if (!raiz || !dados.graficos || !dados.graficos.length) { return; }
    dados.graficos.forEach(function (g) {
      var bloco = el("figure", "painel relatorio-grafico-bloco" + (g.largura === "cheia" ? " cheia" : ""));
      bloco.appendChild(el("figcaption", "relatorio-grafico-titulo", g.titulo));
      if (!g.linhas.length || typeof echarts === "undefined") {
        bloco.appendChild(el("p", "secundario", "Sem dados para este gráfico."));
        raiz.appendChild(bloco);
        return;
      }
      var area = el("div", "relatorio-grafico");
      area.setAttribute("role", "img");
      area.setAttribute("aria-label", g.titulo);
      bloco.appendChild(area);
      raiz.appendChild(bloco);
      var chart = echarts.init(area, null, { renderer: "svg" });
      chart.setOption(T.base(C, opcaoGrafico(g)));
      graficos.push(chart);
    });
    window.addEventListener("resize", function () { graficos.forEach(function (c) { c.resize(); }); });
  }

  /* ---------- tabela ---------- */
  var RE_MOEDA = /valor|total|saldo|r\$|gasto|receita|entrada|sa[ií]da|pre[cç]o|custo/i;

  function montarTabela() {
    var raiz = document.getElementById("relatorio-tabela");
    var t = dados.tabela;
    if (!raiz || !t) { return; }
    raiz.appendChild(el("h2", "", t.titulo));
    if (!t.linhas.length) {
      raiz.appendChild(el("p", "secundario", "Nenhuma linha para mostrar."));
      return;
    }
    var numerica = t.colunas.map(function (_, c) {
      return t.linhas.every(function (l) { return l[c] === null || typeof l[c] === "number"; });
    });
    var moeda = t.colunas.map(function (nome, c) {
      return numerica[c] && RE_MOEDA.test(nome) &&
        t.linhas.every(function (l) { return l[c] === null || Number.isInteger(l[c]); });
    });
    function texto(v, c) {
      if (v === null || v === undefined) { return "—"; }
      if (typeof v === "number") { return moeda[c] ? T.moeda(v) : v.toLocaleString("pt-BR"); }
      return String(v);
    }
    var linhas = t.linhas.map(function (l) {
      var txt = l.map(texto);
      return { cel: l, txt: txt, busca: txt.join(" ").toLowerCase() };
    });

    var barra = el("div", "relatorio-busca");
    var rotulo = el("label", "sr-only", "Buscar na tabela");
    rotulo.htmlFor = "relatorio-busca";
    var busca = el("input");
    busca.type = "search";
    busca.id = "relatorio-busca";
    busca.placeholder = "Buscar na tabela";
    busca.autocomplete = "off";
    var contagem = el("span", "secundario");
    contagem.setAttribute("role", "status");
    barra.appendChild(rotulo);
    barra.appendChild(busca);
    barra.appendChild(contagem);
    raiz.appendChild(barra);

    var rolavel = el("div", "tabela-rolavel");
    rolavel.setAttribute("role", "region");
    rolavel.setAttribute("aria-label", "Tabela: " + t.titulo);
    rolavel.tabIndex = 0;
    var tabela = el("table");
    var cab = el("thead");
    var trc = el("tr");
    var ths = t.colunas.map(function (nome, c) {
      var th = el("th", numerica[c] ? "valor" : "");
      th.scope = "col";
      th.setAttribute("aria-sort", "none");
      var b = el("button", "relatorio-ordenar", nome);
      b.type = "button";
      b.addEventListener("click", function () { ordenar(c); });
      th.appendChild(b);
      trc.appendChild(th);
      return th;
    });
    cab.appendChild(trc);
    var corpo = el("tbody");
    tabela.appendChild(cab);
    tabela.appendChild(corpo);
    rolavel.appendChild(tabela);
    raiz.appendChild(rolavel);
    if (t.truncado) {
      raiz.appendChild(el("p", "secundario", "Mostrando só as primeiras linhas; o resultado completo é maior."));
    }

    var col = -1;
    var asc = true;
    function ordenar(c) {
      asc = col === c ? !asc : true;
      col = c;
      ths.forEach(function (th, i) {
        th.setAttribute("aria-sort", i === c ? (asc ? "ascending" : "descending") : "none");
      });
      linhas.sort(function (a, b) {
        var x = a.cel[c], y = b.cel[c];
        if (x === null || x === undefined) { return y === null || y === undefined ? 0 : 1; }
        if (y === null || y === undefined) { return -1; }
        var r = typeof x === "number" && typeof y === "number" ? x - y : String(x).localeCompare(String(y), "pt-BR", { numeric: true });
        return asc ? r : -r;
      });
      desenhar();
    }

    function desenhar() {
      var q = busca.value.trim().toLowerCase();
      corpo.textContent = "";
      var n = 0;
      linhas.forEach(function (l) {
        if (q && l.busca.indexOf(q) < 0) { return; }
        n += 1;
        var tr = el("tr");
        l.txt.forEach(function (s, c) {
          tr.appendChild(el("td", numerica[c] ? "valor" : "", s));
        });
        corpo.appendChild(tr);
      });
      contagem.textContent = n === 1 ? "1 linha" : n + " linhas";
      if (n === 0) {
        var tr0 = el("tr");
        var td0 = el("td", "secundario", "Nenhuma linha encontrada para esta busca.");
        td0.colSpan = t.colunas.length;
        tr0.appendChild(td0);
        corpo.appendChild(tr0);
      }
    }
    busca.addEventListener("input", desenhar);
    desenhar();
  }

  montarGraficos();
  montarTabela();
  window.addEventListener("pagehide", function () {
    graficos.forEach(function (c) { c.dispose(); });
    graficos.length = 0;
  });
})();
