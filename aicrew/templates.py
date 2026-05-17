"""Minimal Jinja-like template renderer.

Supports:
  * ``{{ variable.path }}`` and ``{{ variable | default('x') }}``
  * ``{% if expr %} ... {% else %} ... {% endif %}``
  * ``{% for item in iterable %} ... {% endfor %}``
  * Truthy expressions over context variables and equality checks ``a == 'b'``.

Not a full Jinja2 – only what our prompt templates need. Same surface area, so
moving to real Jinja2 in production is a drop-in.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

_VAR = re.compile(r"\{\{\s*(.+?)\s*\}\}")
_TAG = re.compile(r"\{%\s*(.+?)\s*%\}")


def _resolve(expr: str, ctx: dict[str, Any]) -> Any:
    expr = expr.strip()
    # default filter
    if "|" in expr:
        head, _, tail = expr.partition("|")
        value = _resolve(head.strip(), ctx)
        tail = tail.strip()
        if tail.startswith("default(") and tail.endswith(")"):
            default = tail[len("default(") : -1].strip()
            if default.startswith(("'", '"')):
                default = default[1:-1]
            return value if value not in (None, "", []) else default
        return value
    if expr.startswith(("'", '"')) and expr.endswith(("'", '"')):
        return expr[1:-1]
    if expr in ("True", "true"):
        return True
    if expr in ("False", "false"):
        return False
    if expr.lstrip("-").isdigit():
        return int(expr)
    cur: Any = ctx
    for part in expr.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            cur = getattr(cur, part, None)
        if cur is None:
            return None
    return cur


def _eval_condition(expr: str, ctx: dict[str, Any]) -> bool:
    expr = expr.strip()
    for op in (" == ", " != "):
        if op in expr:
            left, right = expr.split(op, 1)
            lv = _resolve(left, ctx)
            rv = _resolve(right, ctx)
            return (lv == rv) if op == " == " else (lv != rv)
    val = _resolve(expr, ctx)
    if isinstance(val, (list, tuple, dict, str)):
        return len(val) > 0
    return bool(val)


def render(template: str, ctx: dict[str, Any]) -> str:
    tokens = _tokenize(template)
    out, _ = _render_tokens(tokens, 0, ctx)
    return out


def _tokenize(template: str) -> list[tuple[str, str]]:
    tokens: list[tuple[str, str]] = []
    i = 0
    while i < len(template):
        m_tag = _TAG.search(template, i)
        m_var = _VAR.search(template, i)
        m = None
        for c in (m_tag, m_var):
            if c and (m is None or c.start() < m.start()):
                m = c
        if not m:
            tokens.append(("text", template[i:]))
            break
        if m.start() > i:
            tokens.append(("text", template[i : m.start()]))
        if m is m_tag:
            tokens.append(("tag", m.group(1).strip()))
        else:
            tokens.append(("var", m.group(1).strip()))
        i = m.end()
    return tokens


def _render_tokens(
    tokens: list[tuple[str, str]], i: int, ctx: dict[str, Any], stop: Iterable[str] = ()
) -> tuple[str, int]:
    out: list[str] = []
    while i < len(tokens):
        kind, body = tokens[i]
        if kind == "text":
            out.append(body)
            i += 1
        elif kind == "var":
            value = _resolve(body, ctx)
            out.append("" if value is None else str(value))
            i += 1
        else:  # tag
            head = body.split()[0]
            if head in stop:
                return "".join(out), i
            if head == "if":
                cond = body[len("if") :].strip()
                taken = _eval_condition(cond, ctx)
                inner_true: list[tuple[str, str]] = []
                inner_false: list[tuple[str, str]] = []
                depth = 1
                i += 1
                state = "true"
                while i < len(tokens):
                    k2, b2 = tokens[i]
                    if k2 == "tag":
                        h2 = b2.split()[0]
                        if h2 == "if":
                            depth += 1
                        elif h2 == "endif":
                            depth -= 1
                            if depth == 0:
                                i += 1
                                break
                        elif h2 == "else" and depth == 1:
                            state = "false"
                            i += 1
                            continue
                    (inner_true if state == "true" else inner_false).append(tokens[i])
                    i += 1
                inner = inner_true if taken else inner_false
                rendered, _ = _render_tokens(inner, 0, ctx)
                out.append(rendered)
            elif head == "for":
                m = re.match(r"for\s+(\w+)\s+in\s+(.+)", body)
                if not m:
                    out.append("")
                    i += 1
                    continue
                varname, iterable_expr = m.group(1), m.group(2)
                iterable = _resolve(iterable_expr, ctx) or []
                inner: list[tuple[str, str]] = []
                depth = 1
                i += 1
                while i < len(tokens):
                    k2, b2 = tokens[i]
                    if k2 == "tag":
                        h2 = b2.split()[0]
                        if h2 == "for":
                            depth += 1
                        elif h2 == "endfor":
                            depth -= 1
                            if depth == 0:
                                i += 1
                                break
                    inner.append(tokens[i])
                    i += 1
                for item in iterable:
                    sub_ctx = {**ctx, varname: item}
                    rendered, _ = _render_tokens(inner, 0, sub_ctx)
                    out.append(rendered)
            else:
                # unknown tag – ignore
                i += 1
    return "".join(out), i
