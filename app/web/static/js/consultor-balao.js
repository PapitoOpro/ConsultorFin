(function () {
  var balao = document.getElementById("consultor-balao");
  var painel = document.getElementById("consultor-painel");
  if (!balao || !painel) return;
  var corpo = painel.querySelector(".consultor-corpo");
  var alca = document.getElementById("consultor-alca");
  var CHAVE = "consultor-painel-tamanho";

  function definir(aberto) {
    painel.hidden = !aberto;
    balao.classList.toggle("aberto", aberto);
    balao.setAttribute("aria-expanded", aberto ? "true" : "false");
    if (aberto && !corpo.firstChild) {
      var quadro = document.createElement("iframe");
      quadro.src = painel.dataset.url;
      quadro.title = "Terminal do Consultor";
      quadro.setAttribute("allow", "clipboard-read; clipboard-write");
      corpo.appendChild(quadro);
    }
    if (!aberto) balao.focus();
  }
  document.querySelectorAll("[data-abrir-consultor]").forEach(function (b) {
    b.addEventListener("click", function () { definir(true); });
  });
  balao.addEventListener("click", function () { definir(painel.hidden); });
  document.getElementById("consultor-fechar").addEventListener("click", function () { definir(false); });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && !painel.hidden && document.activeElement && painel.contains(document.activeElement)) definir(false);
  });

  try {
    var salvo = JSON.parse(localStorage.getItem(CHAVE) || "null");
    if (salvo && salvo.w >= 300 && salvo.h >= 320) {
      painel.style.width = salvo.w + "px";
      painel.style.height = salvo.h + "px";
    }
  } catch (e) { /* tamanho padrão */ }

  alca.addEventListener("pointerdown", function (e) {
    e.preventDefault();
    var x0 = e.clientX, y0 = e.clientY, w0 = painel.offsetWidth, h0 = painel.offsetHeight;
    corpo.style.pointerEvents = "none"; /* iframe não engole o arrasto */
    alca.setPointerCapture(e.pointerId);
    function mover(ev) {
      painel.style.width = Math.max(300, w0 + (x0 - ev.clientX)) + "px";
      painel.style.height = Math.max(320, h0 + (y0 - ev.clientY)) + "px";
    }
    function soltar() {
      alca.removeEventListener("pointermove", mover);
      alca.removeEventListener("pointerup", soltar);
      corpo.style.pointerEvents = "";
      try { localStorage.setItem(CHAVE, JSON.stringify({ w: painel.offsetWidth, h: painel.offsetHeight })); } catch (e) { /* sem persistência */ }
    }
    alca.addEventListener("pointermove", mover);
    alca.addEventListener("pointerup", soltar);
  });
})();
