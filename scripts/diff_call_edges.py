#!/usr/bin/env python3
"""Explain which call edges a dependency-free run loses against a full-venv run.

Diffs two ``analysis.json`` files produced from the same source at ``-a 2`` or
higher -- typically a baseline (default venv) and a candidate (``--no-venv`` or
another dependency mode) -- and buckets every call site whose resolution
changed, so a "N% of edges disappeared" number turns into "which mechanism
would win them back"::

    canpy -i APP -a 2 -o out/venv
    canpy -i APP -a 2 -o out/novenv --no-venv
    python scripts/diff_call_edges.py out/venv/analysis.json out/novenv/analysis.json

Three views are reported:

* **All edge families** -- ``call_graph``, ``cfg``, ``cdg``, ``ddg`` (split by
  ``prov``), ``summary``, ``param_in``, ``param_out``: counts and set
  differences, so an L4 run shows where any loss actually lands.
* **Edge level** -- the ``call_graph`` ``(src, dst)`` sets, the number usually
  quoted.
* **Call-site level** -- every ``call`` body node, matched across runs by its
  stable global id. A baseline-resolved site is *kept* (same callee),
  *renamed* (a different callee -- ``canonical-name`` when both are externals
  with the same leaf, i.e. one function reached under a re-export;
  ``external-to-project`` when the baseline homed the project's own code as an
  external because ``pip install -e .`` put it on its own path), or *lost*
  (no callee). Lost sites are bucketed by receiver shape, which is what decides
  the fix: a call on a returned value needs the library's return types, a
  ``self.x()`` call needs the base-class chain, and so on.

Module-scope calls and decorator applications carry no body node, so they show
up only in the edge view. Stdlib only; reads JSON, runs nothing.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Set, Tuple

EXTERNAL = "/@external/"
_IDENT = re.compile(r"^[A-Za-z_]\w*$")
_DOTTED = re.compile(r"^[A-Za-z_]\w*(\.[A-Za-z_]\w*)+$")

# Ordered: the first matching rule names the bucket.
BUCKET_HELP = {
    "constructor": "class instantiation the run could not resolve",
    "bare-name": "plain f(...) call -- import or builtin binding",
    "super-call": "super().x(...) -- needs the base-class chain",
    "self/cls-method": "self.x(...) -- inherited (base class or framework mixin)",
    "subscript-receiver": "x[...].m(...) -- e.g. odoo self.env['model'].m()",
    "call-chain": "f(...).m(...) -- method on a returned value; needs return types",
    "self-attribute": "self.a.m(...) -- attribute/field value; needs attribute types",
    "imported-name": "mod.m(...) / Cls.m(...) on an imported name",
    "local-variable": "v.m(...) on a local or parameter; needs its inferred type",
    "dotted-receiver": "a.b.m(...) on a non-self dotted path",
    "other": "anything else (literals, comprehensions, lambdas, ...)",
}


def _walk_callable(c: Dict[str, Any]) -> Iterator[Dict[str, Any]]:
    yield c
    for inner in (c.get("callables") or {}).values():
        yield from _walk_callable(inner)
    for t in (c.get("types") or {}).values():
        yield from _walk_type(t)


def _walk_type(t: Dict[str, Any]) -> Iterator[Dict[str, Any]]:
    for m in (t.get("callables") or {}).values():
        yield from _walk_callable(m)
    for inner in (t.get("types") or {}).values():
        yield from _walk_type(inner)


def _import_names(module: Dict[str, Any]) -> Set[str]:
    names: Set[str] = set()
    for imp in module.get("imports") or []:
        for key in ("name", "alias"):
            if imp.get(key):
                names.add(imp[key].split(".")[0])
        if imp.get("module"):
            names.add(imp["module"].split(".")[0])
    return names


INTRA_FAMILIES = ("cfg", "cdg", "ddg", "summary")  # per callable; ddg split by prov
APP_FAMILIES = ("call_graph", "param_in", "param_out")  # application scope


def _edge_key(e: Dict[str, Any]) -> str:
    """Edge identity: every field but ``weight``."""
    return json.dumps({k: v for k, v in e.items() if k != "weight"}, sort_keys=True)


class Run:
    """The parts of one analysis.json the diff needs."""

    def __init__(self, path: Path) -> None:
        doc = json.loads(path.read_text(encoding="utf-8"))
        app = doc["application"]
        self.path = path
        self.max_level = doc.get("max_level")
        self.externals: Dict[str, Dict[str, Any]] = app.get("external_symbols") or {}
        self.edges: Dict[Tuple[str, str], Dict[str, Any]] = {
            (e["src"], e["dst"]): e for e in app.get("call_graph") or []
        }
        # every edge family the run emits (L3/L4 included), as identity sets
        self.families: Dict[str, Set[Tuple[str, str]]] = defaultdict(set)
        # site id -> (file key, call node, import-bound names of that file)
        self.sites: Dict[str, Tuple[str, Dict[str, Any], Set[str]]] = {}
        for file_key, module in (app.get("symbol_table") or {}).items():
            imports = _import_names(module)
            roots: List[Dict[str, Any]] = []
            for f in (module.get("functions") or {}).values():
                roots.extend(_walk_callable(f))
            for t in (module.get("types") or {}).values():
                roots.extend(_walk_type(t))
            for c in roots:
                for local, node in (c.get("body") or {}).items():
                    if node.get("kind") == "call":
                        self.sites[node.get("id") or f"{c['id']}@{local}"] = (file_key, node, imports)
                for fam in INTRA_FAMILIES:
                    for e in c.get(fam) or []:
                        name = f"ddg:{','.join(e.get('prov') or [])}" if fam == "ddg" else fam
                        self.families[name].add((c["id"], _edge_key(e)))
        for fam in APP_FAMILIES:
            for e in app.get(fam) or []:
                self.families[fam].add(("", _edge_key(e)))


def classify(node: Dict[str, Any], imports: Set[str]) -> str:
    if node.get("is_constructor_call"):
        return "constructor"
    recv = (node.get("receiver_expr") or "").strip()
    if not recv:
        return "bare-name"
    if recv.startswith("super("):
        return "super-call"
    if recv in ("self", "cls"):
        return "self/cls-method"
    if "[" in recv:
        return "subscript-receiver"
    if "(" in recv:
        return "call-chain"
    if recv.startswith(("self.", "cls.")):
        return "self-attribute"
    if _IDENT.match(recv) or _DOTTED.match(recv):
        if recv.split(".")[0] in imports:
            return "imported-name"
        return "local-variable" if _IDENT.match(recv) else "dotted-receiver"
    return "other"


def target_home(dst: Optional[str], run: Run) -> str:
    """Top-level package of an external target, or ``<project>``."""
    if not dst:
        return "?"
    if EXTERNAL not in dst:
        return "<project>"
    ext = run.externals.get(dst) or {}
    module = ext.get("module") or dst.split(EXTERNAL, 1)[1].rsplit("/", 1)[0]
    return module.replace("/", ".").split(".")[0] or "?"


def leaf(dst: str) -> str:
    """Last name segment, with Jedi's ``Cls/__init__`` folded onto the linker's
    ``Cls`` so a constructor named both ways compares equal."""
    parts = [p for p in re.split(r"[./]", dst.split("(")[0]) if p]
    if len(parts) > 1 and parts[-1] == "__init__":
        parts.pop()
    return parts[-1] if parts else dst


def rename_kind(b: str, c: str) -> str:
    """Why a site resolved in both runs points at different ids."""
    b_ext, c_ext = EXTERNAL in b, EXTERNAL in c
    if b_ext and not c_ext:
        # The baseline reached the project's own code through its installed
        # copy (``pip install -e .``) and homed it as an external.
        return "external-to-project"
    if c_ext and not b_ext:
        return "project-to-external"
    if b_ext and c_ext and leaf(b) == leaf(c):
        return "canonical-name"
    return "different-target"


def short(dst: Optional[str]) -> str:
    if not dst:
        return "None"
    if EXTERNAL in dst:
        return "@external/" + dst.split(EXTERNAL, 1)[1]
    return dst.split("/python/", 1)[-1]


def pct(n: int, d: int) -> str:
    return f"{100.0 * n / d:5.1f}%" if d else "  n/a"


def diff(base: Run, cand: Run, samples: int) -> Dict[str, Any]:
    common = base.sites.keys() & cand.sites.keys()
    status: Counter = Counter()
    lost_by_bucket: Dict[str, List[Tuple[str, Dict[str, Any], str]]] = defaultdict(list)
    lost_home: Counter = Counter()
    lost_bucket_home: Dict[str, Counter] = defaultdict(Counter)
    renamed: List[Tuple[str, str, str, str]] = []
    renamed_targets: Set[Tuple[str, str]] = set()  # (base dst, cand dst)

    for sid in sorted(common):
        file_key, bnode, imports = base.sites[sid]
        _, cnode, _ = cand.sites[sid]
        b, c = bnode.get("callee"), cnode.get("callee")
        if b is None:
            status["gained" if c else "unresolved-in-both"] += 1
            continue
        if c == b:
            status["kept"] += 1
        elif c is not None:
            kind = "renamed:" + rename_kind(b, c)
            status[kind] += 1
            renamed.append((kind, sid, b, c))
            renamed_targets.add((b, c))
        else:
            status["lost"] += 1
            bucket = classify(bnode, imports)
            home = target_home(b, base)
            lost_by_bucket[bucket].append((file_key, bnode, b))
            lost_home[home] += 1
            lost_bucket_home[bucket][home] += 1

    base_edges, cand_edges = set(base.edges), set(cand.edges)
    lost_edges = base_edges - cand_edges
    # An edge "lost" only because its dst is spelled differently in the
    # candidate: same src, and a renamed site maps that dst to one the
    # candidate does have.
    rename_map: Dict[str, Set[str]] = defaultdict(set)
    for b, c in renamed_targets:
        rename_map[b].add(c)
    respelled = {
        (s, d) for (s, d) in lost_edges if any((s, c) in cand_edges for c in rename_map.get(d, ()))
    }

    families = {}
    for fam in sorted(base.families.keys() | cand.families.keys()):
        b, c = base.families.get(fam, set()), cand.families.get(fam, set())
        families[fam] = {"baseline": len(b), "candidate": len(c),
                         "only_baseline": len(b - c), "only_candidate": len(c - b)}

    return {
        "baseline": str(base.path),
        "families": families,
        "candidate": str(cand.path),
        "edges": {
            "baseline": len(base_edges),
            "candidate": len(cand_edges),
            "lost": len(lost_edges),
            "lost_respelled": len(respelled),
            "lost_truly": len(lost_edges - respelled),
            "gained": len(cand_edges - base_edges),
            "lost_by_target_home": dict(
                Counter(target_home(d, base) for (_, d) in lost_edges - respelled).most_common()
            ),
        },
        "sites": {
            "baseline_only": len(base.sites.keys() - cand.sites.keys()),
            "candidate_only": len(cand.sites.keys() - base.sites.keys()),
            "matched": len(common),
            "status": dict(status),
            "lost_by_bucket": {k: len(v) for k, v in sorted(lost_by_bucket.items(), key=lambda kv: -len(kv[1]))},
            "lost_by_target_home": dict(lost_home.most_common()),
            "lost_bucket_x_home": {k: dict(v.most_common()) for k, v in lost_bucket_home.items()},
        },
        "samples": {
            "lost": {
                bucket: [
                    {
                        "at": f"{fk}:{(n.get('span') or {}).get('start', ['?'])[0]}",
                        "call": f"{n.get('receiver_expr') + '.' if n.get('receiver_expr') else ''}{n.get('method_name')}(...)",
                        "baseline_callee": short(b),
                    }
                    for fk, n, b in rows[:samples]
                ]
                for bucket, rows in lost_by_bucket.items()
            },
            "renamed": [
                {"kind": k, "site": sid, "baseline": short(b), "candidate": short(c)}
                for k, sid, b, c in renamed[:samples]
            ],
        },
    }


def render(r: Dict[str, Any], top_homes: int) -> str:
    e, s = r["edges"], r["sites"]
    st = s["status"]
    resolved = sum(v for k, v in st.items() if k not in ("gained", "unresolved-in-both"))
    out = [
        f"baseline : {r['baseline']}",
        f"candidate: {r['candidate']}",
        "",
        "== all edge families (L3/L4 included) ==",
        f"  {'family':<20}{'baseline':>9}{'candidate':>10}{'net':>8}{'only-base':>11}{'only-cand':>11}",
    ]
    tb = tc = 0
    for fam, f in r["families"].items():
        tb, tc = tb + f["baseline"], tc + f["candidate"]
        out.append(
            f"  {fam:<20}{f['baseline']:>9}{f['candidate']:>10}"
            f"{pct(f['candidate'] - f['baseline'], f['baseline']):>8}{f['only_baseline']:>11}{f['only_candidate']:>11}"
        )
    out += [
        f"  {'TOTAL':<20}{tb:>9}{tc:>10}{pct(tc - tb, tb):>8}",
        "",
        "== call_graph edges (src, dst) ==",
        (
            f"  baseline {e['baseline']}   candidate {e['candidate']}   "
            f"net {e['candidate'] - e['baseline']:+d} ({pct(e['candidate'] - e['baseline'], e['baseline']).strip()})"
        ),
        f"  missing from candidate : {e['lost']:6d} {pct(e['lost'], e['baseline'])}",
        (
            f"    re-pointed dst       : {e['lost_respelled']:6d} {pct(e['lost_respelled'], e['baseline'])}"
            "   (same call sites, candidate edge has another dst id)"
        ),
        f"    truly missing        : {e['lost_truly']:6d} {pct(e['lost_truly'], e['baseline'])}",
        f"  new in candidate       : {e['gained']:6d}",
        "  truly-missing edges by target package:",
    ]
    for home, n in list(e["lost_by_target_home"].items())[:top_homes]:
        out.append(f"    {home:<28} {n:6d}")
    out += [
        "",
        "== call sites (body `call` nodes, matched by id) ==",
        f"  matched {s['matched']}   baseline-only {s['baseline_only']}   candidate-only {s['candidate_only']}",
        f"  resolved in baseline   : {resolved:6d}",
    ]
    for k, note in (
        ("kept", ""),
        ("renamed:canonical-name", "same external, spelled differently (re-export / ctor naming)"),
        ("renamed:external-to-project", "baseline homed the project's own code as external"),
        ("renamed:project-to-external", ""),
        ("renamed:different-target", ""),
        ("lost", "no callee in candidate"),
    ):
        out.append(f"    {k:<28} {st.get(k, 0):6d} {pct(st.get(k, 0), resolved)}  {note}".rstrip())
    out.append(f"  resolved only in candidate: {st.get('gained', 0)}")
    out += ["", "== lost call sites by receiver shape (what would recover them) =="]
    for bucket, n in s["lost_by_bucket"].items():
        homes = s["lost_bucket_x_home"][bucket]
        top = ", ".join(f"{h} {c}" for h, c in list(homes.items())[:3])
        out.append(f"  {bucket:<20} {n:6d} {pct(n, st.get('lost', 0))}  {BUCKET_HELP[bucket]}")
        out.append(f"  {'':<20}        top targets: {top}")
    out += ["", "== samples =="]
    for bucket, rows in r["samples"]["lost"].items():
        out.append(f"  [lost: {bucket}]")
        for row in rows:
            out.append(f"    {row['at']:<40} {row['call']:<40} -> {row['baseline_callee']}")
    if r["samples"]["renamed"]:
        out.append("  [renamed]")
        for row in r["samples"]["renamed"]:
            out.append(f"    {row['kind']:<26} {row['baseline']}  =>  {row['candidate']}")
    return "\n".join(out)


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("baseline", type=Path, help="analysis.json from the reference run (e.g. default venv)")
    p.add_argument("candidate", type=Path, help="analysis.json from the run under test (e.g. --no-venv)")
    p.add_argument("--samples", type=int, default=5, help="examples per bucket (default 5)")
    p.add_argument("--top", type=int, default=10, help="target packages to list (default 10)")
    p.add_argument("--json", type=Path, help="also write the full report as JSON here")
    a = p.parse_args(argv)

    base, cand = Run(a.baseline), Run(a.candidate)
    for run in (base, cand):
        if (run.max_level or 0) < 2:
            print(f"error: {run.path} is max_level {run.max_level}; need -a 2 or higher", file=sys.stderr)
            return 2
    report = diff(base, cand, a.samples)
    print(render(report, a.top))
    if a.json:
        a.json.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
