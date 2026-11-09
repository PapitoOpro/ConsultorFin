/* Contagem animada dos valores em reais do Início (respeita movimento reduzido). */
(function () {
  "use strict";
  try { if (matchMedia("(prefers-reduced-motion: reduce)").matches) return; } catch (e) { return; }
  var DURACAO = 700;
  var fmt = new Intl.NumberFormat("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

  function paraCentavos(texto) {
    var m = /^(-\s)?R\$\s([\d.]+),(\d{2})$/.exec(texto.replace(/ /g, " ").trim());
    if (!m) return null;
    var c = parseInt(m[2].replace(/\./g, ""), 10) * 100 + parseInt(m[3], 10);
    return m[1] ? -c : c;
  }
  function formatar(c) {
    var abs = Math.abs(c);
    return (c < 0 ? "- " : "") + "R$ " + fmt.format(abs / 100);
  }
  function animar(el) {
    var original = el.textContent;
    var alvo = paraCentavos(original);
    if (alvo === null || alvo === 0 || formatar(alvo) !== original.replace(/ /g, " ").trim()) return;
    var inicio = null;
    function passo(t) {
      if (inicio === null) inicio = t;
      var p = Math.min(1, (t - inicio) / DURACAO);
      var suave = 1 - Math.pow(1 - p, 3);
      el.textContent = p >= 1 ? original : formatar(Math.round(alvo * suave));
      if (p < 1) requestAnimationFrame(passo);
    }
    requestAnimationFrame(passo);
  }
  function iniciar() {
    document.querySelectorAll(".hero h2 .numero, .grade-kpi .kpi > .valor").forEach(animar);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", iniciar);
  else iniciar();
})();
