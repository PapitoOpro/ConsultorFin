/* Pop-up do cheque especial: uma vez por dia e por estado (localStorage opcional). */
(function () {
  "use strict";
  var dialogo = document.getElementById("cheque-dialogo");
  if (!dialogo || typeof dialogo.showModal !== "function") return;
  var chave = "chequeEspecial:" + dialogo.dataset.dia + ":" + dialogo.dataset.estado;
  try {
    if (window.localStorage.getItem(chave)) return;
  } catch (e) { /* sem localStorage: abre uma vez por carga de página */ }
  dialogo.addEventListener("close", function () {
    try { window.localStorage.setItem(chave, "1"); } catch (e) { /* ignora */ }
  });
  dialogo.showModal();
})();
