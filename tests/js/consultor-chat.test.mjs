import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";

const { parseSSE } = createRequire(import.meta.url)("../../app/web/static/js/consultor-chat.js");

test("quadros completos e resto", () => {
  const r = parseSSE('data: {"tipo":"texto","texto":"a"}\n\ndata: {"tipo":"fim","sessao":"s"}\n\ndata: {"ti');
  assert.deepEqual(r.eventos.map((e) => e.tipo), ["texto", "fim"]);
  assert.equal(r.resto, 'data: {"ti');
});

test("quadro partido entre chunks", () => {
  let r = parseSSE('data: {"tipo":"tex');
  assert.deepEqual(r.eventos, []);
  r = parseSSE(r.resto + 'to","texto":"oi"}\n\n');
  assert.deepEqual(r.eventos, [{ tipo: "texto", texto: "oi" }]);
  assert.equal(r.resto, "");
});

test("CRLF e quadro inválido ignorado", () => {
  const r = parseSSE('data: {"tipo":"status","texto":"x"}\r\n\r\ndata: nao-json\r\n\r\n: comentario\n\ndata: {"sem":1}\n\n');
  assert.deepEqual(r.eventos, [{ tipo: "status", texto: "x" }]);
});

test("separador CRLF partido ao meio", () => {
  let r = parseSSE('data: {"tipo":"fim"}\r\n\r');
  assert.deepEqual(r.eventos, []);
  r = parseSSE(r.resto + "\n");
  assert.deepEqual(r.eventos, [{ tipo: "fim" }]);
});

test("vazio", () => {
  assert.deepEqual(parseSSE(""), { eventos: [], resto: "" });
  assert.deepEqual(parseSSE(null), { eventos: [], resto: "" });
});
