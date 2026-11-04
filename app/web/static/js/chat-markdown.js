/* Markdown seguro do chat. Nunca interpreta HTML nem links: tudo vira texto
 * (createElement + textContent). Varredura linear, sem regex com backtracking. */
(function (root) {
  "use strict";

  var VALOR = /^-?R\$ ?[\d.]+(,\d{2})?$/;

  function inline(s) {
    var out = [];
    var buf = "";
    var i = 0;
    var n = s.length;
    var semFecho = { "**": false, "*": false, "`": false };

    function flush() {
      if (buf) { out.push({ t: "txt", v: buf }); buf = ""; }
    }
    function fecho(marca, de) {
      if (semFecho[marca]) return -1;
      var j = s.indexOf(marca, de);
      if (j === -1) semFecho[marca] = true;
      return j;
    }

    while (i < n) {
      var ch = s.charAt(i);
      var achou = false;
      if (ch === "`") {
        var j = fecho("`", i + 1);
        if (j > i + 1) {
          flush(); out.push({ t: "cod", v: s.slice(i + 1, j) }); i = j + 1; achou = true;
        }
      } else if (ch === "*" && s.charAt(i + 1) === "*") {
        var k = fecho("**", i + 2);
        if (k > i + 2) {
          flush(); out.push({ t: "b", v: s.slice(i + 2, k) }); i = k + 2; achou = true;
        } else {
          buf += "**"; i += 2; achou = true;
        }
      } else if (ch === "*") {
        var m = fecho("*", i + 1);
        if (m > i + 1) {
          flush(); out.push({ t: "i", v: s.slice(i + 1, m) }); i = m + 1; achou = true;
        }
      }
      if (!achou) { buf += ch; i += 1; }
    }
    flush();
    return out;
  }

  function celulas(linha) {
    var t = linha.trim();
    if (t.charAt(0) === "|") t = t.slice(1);
    if (t.charAt(t.length - 1) === "|") t = t.slice(0, -1);
    return t.split("|").map(function (c) { return c.trim(); });
  }

  function ehSeparador(linha) {
    var t = linha.trim();
    if (t.charAt(0) !== "|") return false;
    var cs = celulas(t);
    for (var i = 0; i < cs.length; i++) {
      var c = cs[i];
      if (c.charAt(0) === ":") c = c.slice(1);
      if (c.charAt(c.length - 1) === ":") c = c.slice(0, -1);
      if (c.length === 0) return false;
      for (var j = 0; j < c.length; j++) if (c.charAt(j) !== "-") return false;
    }
    return true;
  }

  function ehLinhaTabela(linha) {
    return linha.trim().charAt(0) === "|";
  }

  /* Retorna {tipo, texto} se a linha e item de lista, senao null. */
  function item(linha) {
    var t = linha.trim();
    if (t.length > 2 && t.charAt(0) === "-" && t.charAt(1) === " ") {
      var r = t.slice(2).trim();
      return r ? { tipo: "ul", texto: r } : null;
    }
    var d = 0;
    while (d < t.length && d < 9 && t.charCodeAt(d) >= 48 && t.charCodeAt(d) <= 57) d++;
    if (d > 0 && t.charAt(d) === "." && t.charAt(d + 1) === " ") {
      var r2 = t.slice(d + 2).trim();
      return r2 ? { tipo: "ol", texto: r2 } : null;
    }
    return null;
  }

  function titulo(linha) {
    var t = linha.trim();
    var d = 0;
    while (d < t.length && d < 6 && t.charAt(d) === "#") d++;
    if (d > 0 && t.charAt(d) === " ") {
      var r = t.slice(d + 1).trim();
      return r ? r : null;
    }
    return null;
  }

  function parseMarkdown(texto) {
    if (typeof texto !== "string" || !texto) return [];
    var linhas = texto.split("\n").map(function (l) { return l.replace(/\r$/, ""); });
    var blocos = [];
    var par = [];

    function fechaPar() {
      if (par.length) {
        blocos.push({ t: "p", c: inline(par.join(" ")) });
        par = [];
      }
    }

    var i = 0;
    while (i < linhas.length) {
      var l = linhas[i];
      if (l.trim() === "") { fechaPar(); i++; continue; }

      var h = titulo(l);
      if (h !== null) {
        fechaPar();
        blocos.push({ t: "p", c: [{ t: "b", v: h }] });
        i++; continue;
      }

      if (ehLinhaTabela(l) && i + 1 < linhas.length && ehSeparador(linhas[i + 1])) {
        fechaPar();
        var cab = celulas(l).map(inline);
        var rows = [];
        i += 2;
        while (i < linhas.length && ehLinhaTabela(linhas[i])) {
          rows.push(celulas(linhas[i]).map(inline));
          i++;
        }
        blocos.push({ t: "tabela", cab: cab, linhas: rows });
        continue;
      }

      var it = item(l);
      if (it) {
        fechaPar();
        var lista = { t: it.tipo, itens: [] };
        while (i < linhas.length) {
          var x = item(linhas[i]);
          if (!x || x.tipo !== lista.t) break;
          lista.itens.push(inline(x.texto));
          i++;
        }
        blocos.push(lista);
        continue;
      }

      par.push(l.trim());
      i++;
    }
    fechaPar();
    return blocos;
  }

  var TAG = { b: "strong", i: "em", cod: "code" };

  function renderInline(inls, pai, doc) {
    for (var i = 0; i < inls.length; i++) {
      var n = inls[i];
      if (n.t === "txt") {
        pai.appendChild(doc.createTextNode(n.v));
      } else {
        var el = doc.createElement(TAG[n.t] || "span");
        el.textContent = n.v;
        pai.appendChild(el);
      }
    }
  }

  function textoPuro(inls) {
    return inls.map(function (n) { return n.v; }).join("");
  }

  function celula(tag, inls, doc) {
    var td = doc.createElement(tag);
    if (tag === "td" && VALOR.test(textoPuro(inls))) td.className = "chat-valor";
    renderInline(inls, td, doc);
    return td;
  }

  function renderBlocos(blocos, doc) {
    var frag = doc.createDocumentFragment();
    for (var i = 0; i < blocos.length; i++) {
      var b = blocos[i];
      if (b.t === "p") {
        var p = doc.createElement("p");
        renderInline(b.c, p, doc);
        frag.appendChild(p);
      } else if (b.t === "ul" || b.t === "ol") {
        var lst = doc.createElement(b.t);
        for (var j = 0; j < b.itens.length; j++) {
          var li = doc.createElement("li");
          renderInline(b.itens[j], li, doc);
          lst.appendChild(li);
        }
        frag.appendChild(lst);
      } else if (b.t === "tabela") {
        var tb = doc.createElement("table");
        var th = doc.createElement("thead");
        var trh = doc.createElement("tr");
        b.cab.forEach(function (c) { trh.appendChild(celula("th", c, doc)); });
        th.appendChild(trh);
        tb.appendChild(th);
        var body = doc.createElement("tbody");
        b.linhas.forEach(function (r) {
          var tr = doc.createElement("tr");
          r.forEach(function (c) { tr.appendChild(celula("td", c, doc)); });
          body.appendChild(tr);
        });
        tb.appendChild(body);
        frag.appendChild(tb);
      }
    }
    return frag;
  }

  var api = { parseMarkdown: parseMarkdown, renderBlocos: renderBlocos };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (root) root.ChatMarkdown = api;
})(typeof window !== "undefined" ? window : null);
