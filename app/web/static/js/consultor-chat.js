/* Chat do Consultor no painel. Texto do servidor só entra no DOM via textContent/createElement. */
(function (root) {
  "use strict";

  var MSG_FALHA = "Não consegui responder agora. Tente de novo.";
  var MSG_REDE = "Sem conexão com o Consultor. Tente de novo.";
  var MSG_HTTP = {
    409: "Aguarde a resposta anterior.",
    422: "Mensagem inválida. Revise o texto e tente de novo.",
    404: "O chat não está disponível no momento."
  };

  /* buffer -> {eventos, resto}. Quadros terminam em linha em branco; aceita CRLF e quadros partidos. */
  function parseSSE(buffer) {
    var texto = String(buffer || "").replace(/\r\n/g, "\n");
    var partes = texto.split("\n\n");
    var resto = partes.pop();
    var eventos = [];
    partes.forEach(function (quadro) {
      var dados = [];
      quadro.split("\n").forEach(function (linha) {
        if (linha.indexOf("data:") !== 0) return;
        var v = linha.slice(5);
        dados.push(v.charAt(0) === " " ? v.slice(1) : v);
      });
      if (!dados.length) return;
      try {
        var ev = JSON.parse(dados.join("\n"));
        if (ev && typeof ev === "object" && typeof ev.tipo === "string") eventos.push(ev);
      } catch (e) { /* quadro inválido é ignorado */ }
    });
    return { eventos: eventos, resto: resto };
  }

  var api = { parseSSE: parseSSE };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (!root || !root.document) return;

  var doc = root.document;
  var lista = doc.getElementById("chat-mensagens");
  var form = doc.getElementById("chat-form");
  var entrada = doc.getElementById("chat-entrada");
  var enviar = doc.getElementById("chat-enviar");
  var nova = doc.getElementById("chat-nova");
  if (!lista || !form || !entrada || !enviar || !nova || !root.ChatMarkdown) return;

  var sessao = null;      /* só em memória: recarregar a página esquece tudo */
  var controle = null;    /* AbortController do stream ativo */
  var turnoAtual = 0;
  var botoesCartao = [];

  function el(tag, classe, texto) {
    var e = doc.createElement(tag);
    if (classe) e.className = classe;
    if (texto !== undefined) e.textContent = texto;
    return e;
  }

  function colado() {
    return lista.scrollHeight - lista.scrollTop - lista.clientHeight < 48;
  }
  function rolar(forcar) {
    if (forcar) lista.scrollTop = lista.scrollHeight;
  }

  function ocupado(sim) {
    entrada.disabled = sim;
    enviar.disabled = sim;
    botoesCartao.forEach(function (b) { if (sim) b.disabled = true; });
    lista.setAttribute("aria-busy", sim ? "true" : "false");
    if (!sim && !doc.hidden && !lista.closest("[hidden]")) entrada.focus();
  }

  function adicionar(no) {
    var seguir = colado();
    lista.appendChild(no);
    rolar(seguir);
    return no;
  }

  function aviso(texto) {
    return adicionar(el("div", "chat-aviso", texto));
  }

  function cartaoRelatorio(ev) {
    var url = String(ev.url || "");
    if (url.indexOf("/relatorio/") !== 0) return;
    var c = el("div", "chat-cartao");
    var a = el("a", "chat-cartao-link", "Abrir relatório: " + String(ev.titulo || "relatório"));
    a.setAttribute("href", url);
    a.setAttribute("target", "_blank");
    a.setAttribute("rel", "noopener");
    c.appendChild(a);
    adicionar(c);
  }

  function cartaoConfirmar(ev) {
    var c = el("div", "chat-cartao");
    c.appendChild(el("p", "chat-cartao-texto", String(ev.descricao || "Confirmar esta ação?")));
    var acoes = el("div", "chat-cartao-acoes");
    var ok = el("button", "chat-botao chat-botao-primario", "Confirmar");
    var nao = el("button", "chat-botao", "Cancelar");
    ok.type = "button";
    nao.type = "button";
    function decidir(aceitar) {
      if (ok.disabled || controle) return;
      ok.disabled = true;
      nao.disabled = true;
      botoesCartao = botoesCartao.filter(function (b) { return b !== ok && b !== nao; });
      iniciarTurno("/consultor/confirmar", { sessao: sessao, token: String(ev.token || ""), aceitar: aceitar });
    }
    ok.addEventListener("click", function () { decidir(true); });
    nao.addEventListener("click", function () { decidir(false); });
    acoes.appendChild(ok);
    acoes.appendChild(nao);
    c.appendChild(acoes);
    ok.disabled = true; /* o stream atual ainda está aberto; liberado em fim() */
    nao.disabled = true;
    botoesCartao.push(ok, nao);
    adicionar(c);
  }

  function liberarCartoes() {
    botoesCartao.forEach(function (b) { b.disabled = false; });
  }

  function iniciarTurno(url, corpo) {
    var id = ++turnoAtual;
    var ctl = new root.AbortController();
    controle = ctl;
    ocupado(true);

    var msg = adicionar(el("div", "chat-msg chat-consultor"));
    var digitando = el("span", "chat-digitando", "digitando…");
    msg.appendChild(digitando);
    var status = null;
    var acumulado = "";
    var quadro = 0;
    var terminou = false;

    function renderizar() {
      quadro = 0;
      var seguir = colado();
      var texto = acumulado.replace(/^\s+/, "");
      while (msg.firstChild) msg.removeChild(msg.firstChild);
      msg.appendChild(root.ChatMarkdown.renderBlocos(root.ChatMarkdown.parseMarkdown(texto), doc));
      rolar(seguir);
    }
    function agendar() {
      if (!quadro) quadro = root.requestAnimationFrame(renderizar);
    }
    function limparStatus() {
      if (status && status.parentNode) status.parentNode.removeChild(status);
      status = null;
    }
    function limparDigitando() {
      if (digitando && digitando.parentNode) digitando.parentNode.removeChild(digitando);
      digitando = null;
    }
    function encerrar() {
      if (quadro) { root.cancelAnimationFrame(quadro); renderizar(); }
      limparStatus();
      limparDigitando();
      if (!acumulado.trim() && msg.parentNode) msg.parentNode.removeChild(msg);
    }
    function falhar(texto) {
      encerrar();
      terminou = true;
      aviso(texto);
    }

    function tratar(ev) {
      if (id !== turnoAtual) return;
      if (ev.tipo === "texto") {
        limparDigitando();
        acumulado += String(ev.texto || "");
        agendar();
      } else if (ev.tipo === "status") {
        var seguir = colado();
        if (!status) status = el("div", "chat-status");
        status.textContent = String(ev.texto || "");
        lista.appendChild(status); /* sempre por último */
        rolar(seguir);
      } else if (ev.tipo === "relatorio") {
        cartaoRelatorio(ev);
      } else if (ev.tipo === "confirmar") {
        cartaoConfirmar(ev);
      } else if (ev.tipo === "fim") {
        if (typeof ev.sessao === "string") sessao = ev.sessao;
        encerrar();
        terminou = true;
      } else if (ev.tipo === "erro") {
        falhar(String(ev.texto || MSG_FALHA));
      }
    }

    function fim() {
      if (id !== turnoAtual) return;
      if (!terminou) falhar(MSG_FALHA);
      controle = null;
      ocupado(false);
      liberarCartoes();
    }

    root.fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", "Accept": "text/event-stream" },
      body: JSON.stringify(corpo),
      credentials: "same-origin",
      signal: ctl.signal
    }).then(function (resp) {
      if (!resp.ok || !resp.body) {
        falhar(MSG_HTTP[resp.status] || MSG_FALHA);
        return;
      }
      var leitor = resp.body.getReader();
      var decodificador = new root.TextDecoder("utf-8");
      var resto = "";
      function ler() {
        return leitor.read().then(function (r) {
          if (id !== turnoAtual) return;
          resto += decodificador.decode(r.value || new Uint8Array(0), { stream: !r.done });
          var p = parseSSE(r.done ? resto + "\n\n" : resto);
          resto = r.done ? "" : p.resto;
          p.eventos.forEach(tratar);
          if (!r.done) return ler();
        });
      }
      return ler();
    }).catch(function (e) {
      if (id !== turnoAtual || (e && e.name === "AbortError")) return;
      falhar(MSG_REDE);
    }).then(fim);
  }

  function ajustarAltura() {
    entrada.style.height = "auto";
    var borda = entrada.offsetHeight - entrada.clientHeight;
    var alvo = entrada.scrollHeight + borda;
    entrada.style.height = Math.min(alvo, 140) + "px";
    entrada.style.overflowY = alvo > 140 ? "auto" : "hidden";
  }

  function submeter() {
    var texto = entrada.value.trim();
    if (!texto || controle) return;
    entrada.value = "";
    ajustarAltura();
    adicionar(el("div", "chat-msg chat-usuario", texto));
    rolar(true);
    iniciarTurno("/consultor/mensagem", { texto: texto, sessao: sessao });
  }

  form.addEventListener("submit", function (e) {
    e.preventDefault();
    submeter();
  });
  entrada.addEventListener("input", ajustarAltura);
  entrada.addEventListener("keydown", function (e) {
    if (e.key !== "Enter" || e.shiftKey || e.isComposing || e.keyCode === 229) return;
    e.preventDefault();
    submeter();
  });

  nova.addEventListener("click", function () {
    turnoAtual++;
    if (controle) controle.abort();
    controle = null;
    sessao = null;
    botoesCartao = [];
    while (lista.firstChild) lista.removeChild(lista.firstChild);
    entrada.value = "";
    ajustarAltura();
    ocupado(false);
    entrada.focus();
  });
})(typeof window !== "undefined" ? window : null);
