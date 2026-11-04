import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";

const { parseMarkdown, renderBlocos } = createRequire(import.meta.url)(
  "../../app/web/static/js/chat-markdown.js",
);

function fakeDoc() {
  const mk = (tag) => {
    const el = {
      tag, children: [], className: "", _text: "",
      appendChild(c) { this.children.push(c); return c; },
      set textContent(v) { this._text = String(v); },
      get textContent() { return this._text; },
    };
    for (const p of ["innerHTML", "outerHTML"]) {
      Object.defineProperty(el, p, { set() { throw new Error(p + " proibido"); } });
    }
    return el;
  };
  return {
    createElement: mk,
    createTextNode: (v) => ({ tag: "#text", _text: String(v), get textContent() { return this._text; } }),
    createDocumentFragment: () => mk("#frag"),
  };
}
const textoDe = (n) => (n.children && n.children.length ? n.children.map(textoDe).join("") : n.textContent);

test("vazio", () => {
  assert.deepEqual(parseMarkdown(""), []);
  assert.deepEqual(parseMarkdown("\n\n  \n"), []);
  assert.deepEqual(parseMarkdown(null), []);
});

test("inline negrito/italico/codigo", () => {
  const [p] = parseMarkdown("a **b** *c* `d` e");
  assert.equal(p.t, "p");
  assert.deepEqual(p.c, [
    { t: "txt", v: "a " }, { t: "b", v: "b" }, { t: "txt", v: " " },
    { t: "i", v: "c" }, { t: "txt", v: " " }, { t: "cod", v: "d" }, { t: "txt", v: " e" },
  ]);
});

test("linhas em branco nas pontas nao geram paragrafos vazios", () => {
  const b = parseMarkdown("\n\nola\n\n");
  assert.equal(b.length, 1);
  assert.deepEqual(b[0].c, [{ t: "txt", v: "ola" }]);
});

test("listas", () => {
  const [u, o] = parseMarkdown("- um\n- **dois**\n\n1. a\n2. b");
  assert.equal(u.t, "ul");
  assert.equal(u.itens.length, 2);
  assert.deepEqual(u.itens[1], [{ t: "b", v: "dois" }]);
  assert.equal(o.t, "ol");
  assert.equal(o.itens.length, 2);
});

test("titulo vira paragrafo em negrito", () => {
  assert.deepEqual(parseMarkdown("## Resumo"), [{ t: "p", c: [{ t: "b", v: "Resumo" }] }]);
});

test("tabela", () => {
  const [t] = parseMarkdown("| Cat | Valor |\n|---|---:|\n| Mercado | R$ 10,00 |\n| Lazer | -R$ 1.200,50 |");
  assert.equal(t.t, "tabela");
  assert.deepEqual(t.cab, [[{ t: "txt", v: "Cat" }], [{ t: "txt", v: "Valor" }]]);
  assert.equal(t.linhas.length, 2);
  assert.deepEqual(t.linhas[1][0], [{ t: "txt", v: "Lazer" }]);
});

test("tabela parcial vira texto", () => {
  for (const s of ["| a | b", "|", "| a | b |\n", "1.", "-"]) {
    const r = parseMarkdown(s);
    assert.ok(r.every((x) => x.t === "p" || x.t === "ul" || x.t === "ol"), s);
  }
});

test("html e links ficam literais", () => {
  const h = "<img src=x onerror=alert(1)>";
  assert.deepEqual(parseMarkdown(h), [{ t: "p", c: [{ t: "txt", v: h }] }]);
  const l = "[x](javascript:alert(1))";
  assert.deepEqual(parseMarkdown(l), [{ t: "p", c: [{ t: "txt", v: l }] }]);
  const f = renderBlocos(parseMarkdown(h + "\n\n" + l), fakeDoc());
  assert.equal(textoDe(f), h + l);
});

test("streaming parcial nao lanca", () => {
  assert.deepEqual(parseMarkdown("**neg"), [{ t: "p", c: [{ t: "txt", v: "**neg" }] }]);
  for (const s of ["*", "**", "`", "***", "`a", "*a**", "**a*"]) {
    assert.doesNotThrow(() => renderBlocos(parseMarkdown(s), fakeDoc()), s);
  }
});

test("render: estrutura, chat-valor e sem innerHTML", () => {
  const doc = fakeDoc();
  const f = renderBlocos(
    parseMarkdown("**x** `y`\n\n- i\n\n| C | V |\n|--|--|\n| a | R$ 10,00 |\n| b | -R$ 1.000 |\n| c | texto |"),
    doc,
  );
  const achar = (n, tag, acc = []) => {
    if (n.tag === tag) acc.push(n);
    (n.children || []).forEach((c) => achar(c, tag, acc));
    return acc;
  };
  assert.equal(achar(f, "strong").length, 1);
  assert.equal(achar(f, "code").length, 1);
  assert.equal(achar(f, "li").length, 1);
  const tds = achar(f, "td");
  assert.equal(tds.length, 6);
  assert.deepEqual(tds.map((t) => t.className.includes("chat-valor")), [false, true, false, true, false, false]);
});

test("entrada adversarial grande termina rapido", () => {
  const casos = [
    "**".repeat(50000), "*".repeat(100000), "`".repeat(100000), "**a ".repeat(25000),
    "*a **b `c ".repeat(10000), "| a ".repeat(25000), "- ".repeat(50000), "[".repeat(100000),
    "|a|\n|-|\n".repeat(10000),
  ];
  for (const c of casos) {
    const t0 = performance.now();
    renderBlocos(parseMarkdown(c), fakeDoc());
    assert.ok(performance.now() - t0 < 500, "lento: " + c.slice(0, 12));
  }
});
