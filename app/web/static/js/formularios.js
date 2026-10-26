/* Indicador de carregamento: <form data-carregando> mostra o spinner no botão de envio e
 * evita envio duplicado durante operações longas (importar, confirmar). Sem dependências. */
(function () {
  "use strict";

  function liberar(form) {
    form.classList.remove("enviando");
    form.removeAttribute("aria-busy");
    form.querySelectorAll("button.carregando").forEach(function (b) {
      b.classList.remove("carregando");
      b.disabled = false;
    });
  }

  document.addEventListener("submit", function (e) {
    var form = e.target;
    if (!form || !form.hasAttribute || !form.hasAttribute("data-carregando") || e.defaultPrevented) { return; }
    var botao = e.submitter || form.querySelector('button[type="submit"]');
    form.classList.add("enviando");
    form.setAttribute("aria-busy", "true");
    if (botao) {
      botao.classList.add("carregando");
      setTimeout(function () { botao.disabled = true; }, 0);
    }
  });

  /* voltar pelo histórico restaura a página; o botão não pode ficar travado */
  window.addEventListener("pageshow", function () {
    document.querySelectorAll("form[data-carregando]").forEach(liberar);
  });
})();
