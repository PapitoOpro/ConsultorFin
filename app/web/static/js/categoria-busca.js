/* Combobox pesquisável para <select data-busca>. Sem dependências.
 * O <select> original continua no DOM (oculto) como fonte da verdade e valor do envio. */
(function (raiz) {
  "use strict";

  var MAX_RESULTADOS = 50;
  var MAX_RECENTES = 6;
  var CHAVE_RECENTES = "consultorfin:categorias-recentes";
  var SEPARADOR = " › ";

  /* ---------- lógica pura ---------- */

  function normalizar(texto) {
    return String(texto == null ? "" : texto)
      .normalize("NFD")
      .replace(/[̀-ͯ]/g, "")
      .toLowerCase()
      .replace(/\s+/g, " ")
      .trim();
  }

  function ehInicioDePalavra(texto, pos) {
    return pos === 0 || !/[a-z0-9]/.test(texto.charAt(pos - 1));
  }

  /* 0 = começa a subcategoria; 1 = começa uma palavra; 2 = substring; -1 = não casa. */
  function posto(rotuloNorm, subNorm, palavra) {
    if (subNorm.indexOf(palavra) === 0) { return 0; }
    var i = rotuloNorm.indexOf(palavra);
    if (i < 0) { return -1; }
    var melhor = 2;
    while (i >= 0) {
      if (ehInicioDePalavra(rotuloNorm, i)) { melhor = 1; break; }
      i = rotuloNorm.indexOf(palavra, i + 1);
    }
    return melhor;
  }

  /* itens: [{id, rotulo, grupo}] -> itens que casam, ordenados por posto (estável). */
  function filtrar(itens, consulta, maximo) {
    var limite = typeof maximo === "number" ? maximo : MAX_RESULTADOS;
    var palavras = normalizar(consulta).split(" ").filter(Boolean);
    if (palavras.length === 0) { return itens.slice(0, limite); }
    var achados = [];
    for (var n = 0; n < itens.length; n++) {
      var rotuloNorm = normalizar(itens[n].rotulo);
      var corte = rotuloNorm.lastIndexOf(normalizar(SEPARADOR.trim()));
      var subNorm = corte >= 0 ? rotuloNorm.slice(corte + 1).trim() : rotuloNorm;
      var pior = 0;
      for (var k = 0; k < palavras.length && pior >= 0; k++) {
        var p = posto(rotuloNorm, subNorm, palavras[k]);
        pior = p < 0 ? -1 : Math.max(pior, p);
      }
      if (pior >= 0) { achados.push({ item: itens[n], posto: pior, ordem: n }); }
    }
    achados.sort(function (a, b) { return a.posto - b.posto || a.ordem - b.ordem; });
    return achados.slice(0, limite).map(function (a) { return a.item; });
  }

  /* Enter só confirma escolha explícita: consulta digitada ou navegação/hover/clique na lista. */
  function deveConfirmar(consulta, interagiu) {
    return normalizar(consulta) !== "" || interagiu === true;
  }

  /* Posição da lista aberta (portal, position: fixed) a partir do retângulo do campo e da janela.
     Escolhe o lado com mais espaço (abaixo se couber um mínimo útil); altura máxima limitada a `teto`. */
  var MARGEM_LISTA = 8;
  var ALTURA_MINIMA_ABAIXO = 200;
  var ALTURA_MAXIMA_LISTA = 320;
  var LARGURA_MAXIMA_LISTA = 420;
  /* largura/altura: área útil do layout (clientWidth/clientHeight, sem barras de rolagem);
     vv (opcional): visual viewport {left, top, width, height}, que restringe ainda mais a área. */
  function posicionarLista(rect, largura, altura, vv) {
    var a0 = vv ? vv.left : 0;
    var t0 = vv ? vv.top : 0;
    var a1 = vv ? Math.min(largura, vv.left + vv.width) : largura;
    var b1 = vv ? Math.min(altura, vv.top + vv.height) : altura;
    var abaixo = b1 - rect.bottom - MARGEM_LISTA;
    var acima = rect.top - t0 - MARGEM_LISTA;
    var lado = (abaixo >= ALTURA_MINIMA_ABAIXO || abaixo >= acima) ? "abaixo" : "acima";
    var espaco = Math.max(0, lado === "abaixo" ? abaixo : acima);
    var minimo = Math.max(0, rect.right - rect.left);
    var maxLarg = Math.max(minimo, Math.min(LARGURA_MAXIMA_LISTA, a1 - a0 - 2 * MARGEM_LISTA));
    var left = Math.max(a0 + MARGEM_LISTA, Math.min(rect.left, a1 - MARGEM_LISTA - minimo));
    var r = { lado: lado, left: left, minWidth: minimo, maxWidth: maxLarg,
              maxHeight: Math.min(ALTURA_MAXIMA_LISTA, espaco) };
    if (lado === "abaixo") { r.top = rect.bottom + 4; } else { r.bottom = altura - rect.top + 4; }
    return r;
  }

  /* O campo continua "à vista"? Metade (ou mais) da altura e da largura dentro da área visível
     e, se houver, dentro do contêiner rolável. Retângulos: {top, bottom, left, right}. */
  function sobreposicao(a0, a1, b0, b1) { return Math.min(a1, b1) - Math.max(a0, b0); }
  function entradaVisivel(campo, area, contenedor) {
    var alt = campo.bottom - campo.top;
    var larg = campo.right - campo.left;
    function dentro(r) {
      return sobreposicao(campo.top, campo.bottom, r.top, r.bottom) >= alt / 2 &&
             sobreposicao(campo.left, campo.right, r.left, r.right) >= larg / 2;
    }
    return dentro(area) && (!contenedor || dentro(contenedor));
  }

  var API = { normalizar: normalizar, filtrar: filtrar, deveConfirmar: deveConfirmar,
              posicionarLista: posicionarLista, entradaVisivel: entradaVisivel };
  raiz.ConsultorFinBusca = API;
  if (typeof module !== "undefined" && module.exports) { module.exports = API; }

  /* ---------- DOM ---------- */

  if (typeof document === "undefined") { return; }

  var contador = 0;
  var focoPendente = null; /* id da <tr> cujo combobox deve recuperar o foco após o swap */
  var TEMPO_FOCO_MS = 3000;
  var portais = []; /* listas abertas no <body>: [{input, lista, wrap}] */
  var timerFoco = null;

  function definirFocoPendente(id) {
    focoPendente = id;
    if (timerFoco) { clearTimeout(timerFoco); timerFoco = null; }
    if (id) { timerFoco = setTimeout(function () { focoPendente = null; }, TEMPO_FOCO_MS); }
  }

  function lerRecentes() {
    try {
      var bruto = window.localStorage.getItem(CHAVE_RECENTES);
      var lista = bruto ? JSON.parse(bruto) : [];
      return Array.isArray(lista) ? lista.filter(function (x) { return /^\d+$/.test(String(x)); }) : [];
    } catch (e) { return []; }
  }

  function gravarRecente(id) {
    if (!/^\d+$/.test(String(id))) { return; }
    try {
      var lista = lerRecentes().filter(function (x) { return String(x) !== String(id); });
      lista.unshift(String(id));
      window.localStorage.setItem(CHAVE_RECENTES, JSON.stringify(lista.slice(0, 20)));
    } catch (e) { /* localStorage indisponível: segue sem recentes */ }
  }

  function rotuloDoSelect(select) {
    var aria = select.getAttribute("aria-label");
    if (aria) { return aria; }
    if (select.id) {
      var lab = document.querySelector('label[for="' + select.id + '"]');
      if (lab && lab.textContent) { return lab.textContent.trim(); }
    }
    return select.name || "Categoria";
  }

  function lerItens(select) {
    var itens = [];
    Array.prototype.forEach.call(select.options, function (op) {
      var pai = op.parentNode;
      var grupo = pai && pai.tagName === "OPTGROUP" ? pai.label : "";
      itens.push({ id: op.value, rotulo: op.textContent.trim(), grupo: grupo });
    });
    return itens;
  }

  function marcaTipo(grupo) {
    var m = /\((entrada|saída)\)\s*$/.exec(grupo || "");
    return m ? "(" + m[1] + ")" : "";
  }

  function el(tag, classe, texto) {
    var e = document.createElement(tag);
    if (classe) { e.className = classe; }
    if (texto != null) { e.textContent = texto; }
    return e;
  }

  function areaUtil() {
    var raizDoc = document.documentElement;
    var v = window.visualViewport;
    return {
      largura: raizDoc.clientWidth, altura: raizDoc.clientHeight,
      vv: v ? { left: v.offsetLeft, top: v.offsetTop, width: v.width, height: v.height } : null
    };
  }

  function campoVisivel(input) {
    var a = areaUtil();
    var area = { top: a.vv ? a.vv.top : 0, left: a.vv ? a.vv.left : 0,
                 bottom: a.vv ? Math.min(a.altura, a.vv.top + a.vv.height) : a.altura,
                 right: a.vv ? Math.min(a.largura, a.vv.left + a.vv.width) : a.largura };
    var rolavel = input.closest ? input.closest(".tabela-rolavel") : null;
    return entradaVisivel(input.getBoundingClientRect(), area, rolavel ? rolavel.getBoundingClientRect() : null);
  }

  function aplicarPosicao(input, lista) {
    var area = areaUtil();
    var pos = posicionarLista(input.getBoundingClientRect(), area.largura, area.altura, area.vv);
    var st = lista.style;
    st.left = pos.left + "px";
    st.minWidth = pos.minWidth + "px";
    st.maxWidth = pos.maxWidth + "px";
    st.maxHeight = pos.maxHeight + "px";
    st.top = pos.lado === "abaixo" ? pos.top + "px" : "auto";
    st.bottom = pos.lado === "acima" ? pos.bottom + "px" : "auto";
    var larg = lista.offsetWidth; /* largura real (conteúdo): evita sair pela direita */
    var limiteD = areaUtil().largura;
    if (larg && pos.left + larg > limiteD - 8) {
      st.left = Math.max(8, limiteD - 8 - larg) + "px";
    }
  }

  function devolverLista(entrada) {
    var i = portais.indexOf(entrada);
    if (i >= 0) { portais.splice(i, 1); }
    var lista = entrada.lista;
    lista.classList.remove("portal");
    lista.removeAttribute("style");
    if (entrada.wrap.isConnected) { entrada.wrap.appendChild(lista); }
    else if (lista.parentNode) { lista.parentNode.removeChild(lista); }
  }

  function reposicionarPortais() {
    portais.slice().forEach(function (e) {
      if (!e.input.isConnected) { e.lista.hidden = true; devolverLista(e); return; }
      if (!campoVisivel(e.input)) { e.fechar(); return; }
      aplicarPosicao(e.input, e.lista);
    });
  }

  function limparOrfas() {
    portais.slice().forEach(function (e) { if (!e.input.isConnected) { devolverLista(e); } });
  }

  window.addEventListener("scroll", function (ev) {
    if (portais.length && !(ev.target && ev.target.classList && ev.target.classList.contains("busca-lista"))) {
      reposicionarPortais();
    }
  }, true);
  window.addEventListener("resize", reposicionarPortais);

  function atualizar(select) {
    if (select.getAttribute("data-busca-ok")) { return; }
    select.setAttribute("data-busca-ok", "1");
    contador += 1;
    var uid = "busca-" + contador;

    var wrap = el("span", "busca");
    var input = el("input", "busca-input");
    input.type = "text";
    input.id = uid + "-input";
    input.autocomplete = "off";
    input.setAttribute("role", "combobox");
    input.setAttribute("aria-autocomplete", "list");
    input.setAttribute("aria-expanded", "false");
    input.setAttribute("aria-controls", uid + "-lista");
    input.setAttribute("aria-label", rotuloDoSelect(select));
    var lista = el("ul", "busca-lista");
    lista.id = uid + "-lista";
    lista.setAttribute("role", "listbox");
    lista.setAttribute("aria-label", rotuloDoSelect(select));
    lista.hidden = true;
    wrap.appendChild(input);
    wrap.appendChild(lista);

    select.classList.add("busca-oculto");
    select.tabIndex = -1;
    select.setAttribute("aria-hidden", "true");
    select.parentNode.insertBefore(wrap, select);
    wrap.appendChild(select); /* contido no wrapper relativo: não gera rolagem horizontal */

    var itens = lerItens(select);
    var linhas = []; /* [{li, item}] opções visíveis */
    var ativo = -1;
    var aberto = false;
    var suprimirAbrir = false;
    var interagiu = false; /* ↓/↑, hover ou clique na lista desde a abertura / última digitação */
    var compondo = false;  /* IME / tecla morta em composição */

    function rotuloAtual() {
      var op = select.options[select.selectedIndex];
      return op ? op.textContent.trim() : "";
    }
    function mostrarRotulo() { input.value = rotuloAtual(); }
    function sincronizarDesabilitado() { input.disabled = select.disabled; }

    function destacar(i) {
      if (ativo >= 0 && linhas[ativo]) {
        linhas[ativo].li.classList.remove("ativo");
        linhas[ativo].li.setAttribute("aria-selected", "false");
      }
      ativo = i;
      if (i >= 0 && linhas[i]) {
        linhas[i].li.classList.add("ativo");
        linhas[i].li.setAttribute("aria-selected", "true");
        input.setAttribute("aria-activedescendant", linhas[i].li.id);
        if (linhas[i].li.scrollIntoView) { linhas[i].li.scrollIntoView({ block: "nearest" }); }
      } else {
        input.removeAttribute("aria-activedescendant");
      }
    }

    function criarOpcao(item, n) {
      var li = el("li", "busca-opcao");
      li.id = uid + "-op-" + n;
      li.setAttribute("role", "option");
      li.setAttribute("aria-selected", "false");
      li.appendChild(el("span", "busca-texto", item.rotulo));
      var tipo = marcaTipo(item.grupo);
      if (tipo) { li.appendChild(el("span", "busca-tipo", tipo)); }
      li.addEventListener("mousedown", function (ev) { ev.preventDefault(); });
      li.addEventListener("mousemove", function () {
        var i = linhas.findIndex(function (l) { return l.li === li; });
        if (i >= 0 && i !== ativo) { destacar(i); }
        interagiu = true;
      });
      li.addEventListener("click", function () { escolher(item); });
      return li;
    }

    function desenhar(consulta) {
      while (lista.firstChild) { lista.removeChild(lista.firstChild); }
      linhas = [];
      var n = 0;
      var vazia = normalizar(consulta) === "";
      function adicionar(item) {
        var li = criarOpcao(item, n++);
        lista.appendChild(li);
        linhas.push({ li: li, item: item });
      }
      if (vazia) {
        var existentes = {};
        itens.forEach(function (it) { existentes[it.id] = it; });
        var recentes = lerRecentes().filter(function (id) { return existentes[id]; }).slice(0, MAX_RECENTES);
        if (recentes.length) {
          var cab = el("li", "busca-titulo", "Recentes");
          cab.setAttribute("role", "presentation");
          lista.appendChild(cab);
          recentes.forEach(function (id) { adicionar(existentes[id]); });
          var cab2 = el("li", "busca-titulo", "Todas");
          cab2.setAttribute("role", "presentation");
          lista.appendChild(cab2);
        }
      }
      var casados = filtrar(itens, vazia ? "" : consulta, MAX_RESULTADOS);
      casados.forEach(adicionar);
      if (!casados.length) {
        var nada = el("li", "busca-nada", "Nenhum resultado");
        nada.setAttribute("role", "presentation");
        lista.appendChild(nada);
      } else if (vazia && itens.length > casados.length) {
        var dica = el("li", "busca-nada", "Digite para filtrar (" + itens.length + " opções)");
        dica.setAttribute("role", "presentation");
        lista.appendChild(dica);
      }
      if (vazia) {
        var atual = -1;
        for (var q = 0; q < linhas.length; q++) {
          if (linhas[q].item.id === select.value) { atual = q; break; }
        }
        destacar(atual);
      } else {
        destacar(linhas.length ? 0 : -1);
      }
      if (aberto) { aplicarPosicao(input, lista); }
    }

    var entrada = { input: input, lista: lista, wrap: wrap, fechar: function () { fechar(); } };
    function recolher() { /* devolve a lista ao wrapper (sai do portal) */
      if (portais.indexOf(entrada) >= 0) { devolverLista(entrada); }
    }

    function abrir() {
      if (aberto || input.disabled) { return; }
      aberto = true;
      lista.hidden = false;
      limparOrfas();
      lista.classList.add("portal");
      document.body.appendChild(lista);
      portais.push(entrada);
      aplicarPosicao(input, lista);
      input.setAttribute("aria-expanded", "true");
      interagiu = false;
      desenhar("");
    }
    function fechar() {
      if (!aberto) { return; }
      aberto = false;
      lista.hidden = true;
      recolher();
      input.setAttribute("aria-expanded", "false");
      input.removeAttribute("aria-activedescendant");
      mostrarRotulo();
    }

    function escolher(item) {
      var mudou = select.value !== item.id;
      aberto = false;
      lista.hidden = true;
      recolher();
      input.setAttribute("aria-expanded", "false");
      gravarRecente(item.id);
      if (mudou) {
        var tr = select.closest ? select.closest("tr[id]") : null;
        var viaHtmx = select.hasAttribute("hx-post") || select.hasAttribute("hx-trigger");
        definirFocoPendente(tr && viaHtmx ? tr.id : null);
        select.value = item.id;
        mostrarRotulo();
        select.dispatchEvent(new Event("change", { bubbles: true }));
      } else {
        mostrarRotulo();
      }
    }

    input.addEventListener("focus", function () {
      if (suprimirAbrir) { suprimirAbrir = false; return; }
      input.select();
      abrir();
    });
    input.addEventListener("click", function () { abrir(); });
    input.addEventListener("compositionstart", function () { compondo = true; });
    input.addEventListener("compositionend", function () {
      compondo = false;
      if (!aberto) { abrir(); }
      interagiu = false;
      desenhar(input.value);
    });
    input.addEventListener("input", function () {
      if (compondo) { return; }
      if (!aberto) { abrir(); }
      interagiu = false;
      desenhar(input.value);
    });
    input.addEventListener("blur", fechar);
    input.addEventListener("keydown", function (ev) {
      var k = ev.key;
      if (ev.isComposing || ev.keyCode === 229 || compondo) { return; }
      if (k === "ArrowDown" || k === "ArrowUp") {
        ev.preventDefault();
        if (!aberto) { abrir(); return; }
        if (!linhas.length) { return; }
        interagiu = true;
        var passo = k === "ArrowDown" ? 1 : -1;
        if (ativo < 0) { destacar(passo > 0 ? 0 : linhas.length - 1); }
        else { destacar((ativo + passo + linhas.length) % linhas.length); }
      } else if (k === "Enter") {
        if (aberto) {
          ev.preventDefault();
          if (deveConfirmar(input.value, interagiu) && ativo >= 0 && linhas[ativo]) {
            escolher(linhas[ativo].item);
          } else {
            fechar();
          }
        }
      } else if (k === "Escape") {
        if (aberto) { ev.preventDefault(); fechar(); input.select(); }
      }
    });

    lista.addEventListener("mousedown", function (ev) { ev.preventDefault(); });
    if (select.form) {
      select.form.addEventListener("reset", function () { setTimeout(mostrarRotulo, 0); });
    }
    select.addEventListener("change", mostrarRotulo);
    select.addEventListener("focus", function () { input.focus(); });
    select.addEventListener("invalid", function () { input.focus(); });
    if (typeof MutationObserver !== "undefined") {
      new MutationObserver(sincronizarDesabilitado)
        .observe(select, { attributes: true, attributeFilter: ["disabled"] });
    }

    mostrarRotulo();
    sincronizarDesabilitado();

    var tr0 = select.closest ? select.closest("tr[id]") : null;
    if (focoPendente && tr0 && tr0.id === focoPendente && !input.disabled) {
      focoPendente = null;
      suprimirAbrir = true;
      input.focus();
      suprimirAbrir = false;
    }
  }

  function iniciar(contexto) {
    var base = contexto && contexto.querySelectorAll ? contexto : document;
    if (base.matches && base.matches("select[data-busca]")) { atualizar(base); }
    Array.prototype.forEach.call(base.querySelectorAll("select[data-busca]"), atualizar);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () { iniciar(document); });
  } else {
    iniciar(document);
  }
  document.addEventListener("htmx:responseError", function () { definirFocoPendente(null); });
  document.addEventListener("htmx:sendError", function () { definirFocoPendente(null); });
  document.addEventListener("htmx:load", function (ev) { limparOrfas(); iniciar(ev.target); });
  document.addEventListener("htmx:afterSwap", limparOrfas);
})(typeof window !== "undefined" ? window : globalThis);
