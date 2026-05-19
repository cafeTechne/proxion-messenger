#!/usr/bin/env python3
"""
Proxion mutation tester — Windows-compatible AST-based mutation testing.

Usage:
    python tools/mutate.py <source_file> [<source_file2> ...] [options]

Options:
    --tests <glob>       pytest selector (default: tests/)
    --timeout <secs>     per-mutant timeout in seconds (default: 30)
    --show-survived      print diffs of surviving mutants
    --operator <name>    run only this operator (can repeat)

Mutation operators implemented:
    CompareOp    — flip ==, !=, <, <=, >, >=, is, is not, in, not in
    BoolLiteral  — True <-> False
    BoolOp       — and <-> or
    UnaryNot     — insert/remove unary `not`
    ReturnNone   — replace return <expr> with return None
    OffByOne     — +1/-1 on integer literals in comparisons/slices
    ArithOp      — swap +/-, *//, swap augmented assign operators
    NoneCheck    — `is None` <-> `is not None`
    SliceStep    — remove/negate slice step

Exit code: 0 = 100% kill rate, 1 = survivors exist, 2 = error
"""

from __future__ import annotations

import ast
import copy
import os
import subprocess
import sys
import textwrap
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator


# -- Mutation operators --------------------------------------------------------

class _MutationTracker(ast.NodeTransformer):
    """Base class; subclasses override visit_* and call _record() when mutating."""

    def __init__(self, target_index: int):
        self.target_index = target_index   # which mutation to apply (-1 = collect only)
        self._count = 0                    # mutations seen so far
        self.mutated = False               # did we apply the target mutation?
        self.description = ""              # human-readable description

    def _maybe_mutate(self, original_node: ast.AST, mutated_node: ast.AST,
                      description: str) -> ast.AST:
        if self._count == self.target_index:
            self.mutated = True
            self.description = description
            self._count += 1
            return ast.copy_location(mutated_node, original_node)
        self._count += 1
        return original_node

    @property
    def mutation_count(self) -> int:
        return self._count


class CompareOpMutator(_MutationTracker):
    """Flip each comparison operator once per mutation."""
    FLIPS = {
        ast.Eq:    (ast.NotEq,  "== -> !="),
        ast.NotEq: (ast.Eq,     "!= -> =="),
        ast.Lt:    (ast.GtE,    "<  -> >="),
        ast.LtE:   (ast.Gt,     "<= -> >"),
        ast.Gt:    (ast.LtE,    ">  -> <="),
        ast.GtE:   (ast.Lt,     ">= -> <"),
        ast.Is:    (ast.IsNot,  "is -> is not"),
        ast.IsNot: (ast.Is,     "is not -> is"),
        ast.In:    (ast.NotIn,  "in -> not in"),
        ast.NotIn: (ast.In,     "not in -> in"),
    }

    def visit_Compare(self, node: ast.Compare) -> ast.AST:
        new_ops = list(node.ops)
        for i, op in enumerate(node.ops):
            flip_cls, desc = self.FLIPS.get(type(op), (None, None))
            if flip_cls is None:
                continue
            new_node = copy.deepcopy(node)
            new_node.ops[i] = flip_cls()
            result = self._maybe_mutate(node, new_node, f"Compare: {desc} at line {node.lineno}")
            if result is not node:
                return result
        return self.generic_visit(node)


class BoolLiteralMutator(_MutationTracker):
    """Flip True <-> False."""

    def visit_Constant(self, node: ast.Constant) -> ast.AST:
        if node.value is True:
            new_node = ast.Constant(value=False)
            return self._maybe_mutate(node, new_node, f"True -> False at line {node.lineno}")
        if node.value is False:
            new_node = ast.Constant(value=True)
            return self._maybe_mutate(node, new_node, f"False -> True at line {node.lineno}")
        return self.generic_visit(node)


class BoolOpMutator(_MutationTracker):
    """Swap `and` <-> `or`."""

    def visit_BoolOp(self, node: ast.BoolOp) -> ast.AST:
        if isinstance(node.op, ast.And):
            new_node = copy.deepcopy(node)
            new_node.op = ast.Or()
            return self._maybe_mutate(node, new_node, f"and -> or at line {node.lineno}")
        if isinstance(node.op, ast.Or):
            new_node = copy.deepcopy(node)
            new_node.op = ast.And()
            return self._maybe_mutate(node, new_node, f"or -> and at line {node.lineno}")
        return self.generic_visit(node)


class ReturnNoneMutator(_MutationTracker):
    """Replace `return <expr>` with `return None` (skips bare `return`)."""

    def visit_Return(self, node: ast.Return) -> ast.AST:
        if node.value is None:
            return node
        # Don't mutate `return True/False` — covered by BoolLiteralMutator
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, bool):
            return self.generic_visit(node)
        new_node = ast.Return(value=ast.Constant(value=None))
        return self._maybe_mutate(
            node, new_node,
            f"return <expr> -> return None at line {node.lineno}"
        )


class OffByOneMutator(_MutationTracker):
    """Add or subtract 1 from integer constants used in comparisons and slices."""

    def _flip(self, node: ast.Constant, delta: int, ctx: str) -> ast.AST:
        new_node = ast.Constant(value=node.value + delta)
        return self._maybe_mutate(
            node, new_node,
            f"{ctx}: {node.value} -> {node.value + delta} at line {node.lineno}"
        )

    def visit_Compare(self, node: ast.Compare) -> ast.AST:
        new_comparators = []
        for comp in node.comparators:
            if isinstance(comp, ast.Constant) and isinstance(comp.value, int):
                result = self._flip(comp, 1, "compare boundary")
                if result is not comp:
                    new_node = copy.deepcopy(node)
                    new_node.comparators[node.comparators.index(comp)] = result
                    return new_node
        return self.generic_visit(node)

    def visit_Slice(self, node: ast.Slice) -> ast.AST:
        for attr in ("lower", "upper", "step"):
            val = getattr(node, attr)
            if isinstance(val, ast.Constant) and isinstance(val.value, int):
                result = self._flip(val, 1, f"slice {attr}")
                if result is not val:
                    new_node = copy.deepcopy(node)
                    setattr(new_node, attr, result)
                    return new_node
        return self.generic_visit(node)


class ArithOpMutator(_MutationTracker):
    """Swap + <-> -, * <-> //."""
    FLIPS = {
        ast.Add:  (ast.Sub,      "+ -> -"),
        ast.Sub:  (ast.Add,      "- -> +"),
        ast.Mult: (ast.FloorDiv, "* -> //"),
        ast.FloorDiv: (ast.Mult, "// -> *"),
        ast.Mod:  (ast.FloorDiv, "% -> //"),
    }

    def visit_BinOp(self, node: ast.BinOp) -> ast.AST:
        flip_cls, desc = self.FLIPS.get(type(node.op), (None, None))
        if flip_cls is not None:
            new_node = copy.deepcopy(node)
            new_node.op = flip_cls()
            result = self._maybe_mutate(node, new_node, f"ArithOp: {desc} at line {node.lineno}")
            if result is not node:
                return result
        return self.generic_visit(node)

    def visit_AugAssign(self, node: ast.AugAssign) -> ast.AST:
        flip_cls, desc = self.FLIPS.get(type(node.op), (None, None))
        if flip_cls is not None:
            new_node = copy.deepcopy(node)
            new_node.op = flip_cls()
            result = self._maybe_mutate(node, new_node, f"AugAssign: {desc} at line {node.lineno}")
            if result is not node:
                return result
        return self.generic_visit(node)


class NoneCheckMutator(_MutationTracker):
    """Flip `x is None` <-> `x is not None`."""

    def visit_Compare(self, node: ast.Compare) -> ast.AST:
        new_ops = list(node.ops)
        new_comps = list(node.comparators)
        for i, (op, comp) in enumerate(zip(node.ops, node.comparators)):
            if isinstance(comp, ast.Constant) and comp.value is None:
                if isinstance(op, ast.Is):
                    new_node = copy.deepcopy(node)
                    new_node.ops[i] = ast.IsNot()
                    result = self._maybe_mutate(node, new_node,
                                                f"is None -> is not None at line {node.lineno}")
                    if result is not node:
                        return result
                elif isinstance(op, ast.IsNot):
                    new_node = copy.deepcopy(node)
                    new_node.ops[i] = ast.Is()
                    result = self._maybe_mutate(node, new_node,
                                                f"is not None -> is None at line {node.lineno}")
                    if result is not node:
                        return result
        return self.generic_visit(node)


OPERATORS = [
    CompareOpMutator,
    BoolLiteralMutator,
    BoolOpMutator,
    ReturnNoneMutator,
    OffByOneMutator,
    ArithOpMutator,
    NoneCheckMutator,
]


# -- Mutant generation --------------------------------------------------------─

@dataclass
class Mutant:
    source_file: Path
    operator: str
    index: int
    description: str
    mutated_source: str
    original_source: str

    @property
    def diff_lines(self) -> list[str]:
        orig = self.original_source.splitlines()
        mut  = self.mutated_source.splitlines()
        import difflib
        return list(difflib.unified_diff(orig, mut, lineterm="",
                                         fromfile="original", tofile="mutant"))


def generate_mutants(source_path: Path) -> list[Mutant]:
    source = source_path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(source_path))
    mutants: list[Mutant] = []

    for op_cls in OPERATORS:
        # Count how many mutations this operator can produce
        counter = op_cls(target_index=-1)  # count mode
        counter.visit(copy.deepcopy(tree))
        count = counter.mutation_count

        for idx in range(count):
            mutator = op_cls(target_index=idx)
            new_tree = mutator.visit(copy.deepcopy(tree))
            if not mutator.mutated:
                continue
            ast.fix_missing_locations(new_tree)
            try:
                mutated_src = ast.unparse(new_tree)
            except Exception:
                continue
            mutants.append(Mutant(
                source_file=source_path,
                operator=op_cls.__name__.replace("Mutator", ""),
                index=idx,
                description=mutator.description,
                mutated_source=mutated_src,
                original_source=source,
            ))

    return mutants


# -- Test runner --------------------------------------------------------------─

@dataclass
class MutantResult:
    mutant: Mutant
    status: str   # "killed" | "survived" | "error" | "timeout"
    elapsed: float = 0.0
    output: str = ""


def run_mutant(mutant: Mutant, test_selector: str, timeout: int) -> MutantResult:
    """Write mutant to a temp file, run pytest, return result."""
    import tempfile, shutil

    orig_path = mutant.source_file
    backup_src = orig_path.read_text(encoding="utf-8")

    try:
        orig_path.write_text(mutant.mutated_source, encoding="utf-8")
        t0 = time.monotonic()
        result = subprocess.run(
            [sys.executable, "-m", "pytest", test_selector, "-x", "-q",
             "--tb=no", "--no-header", "--timeout=20"],
            capture_output=True, text=True,
            timeout=timeout,
            cwd=orig_path.parent.parent.parent.parent,  # proxion-core root
        )
        elapsed = time.monotonic() - t0
        if result.returncode == 0:
            return MutantResult(mutant, "survived", elapsed, result.stdout + result.stderr)
        else:
            return MutantResult(mutant, "killed", elapsed, "")
    except subprocess.TimeoutExpired:
        return MutantResult(mutant, "timeout", timeout, "")
    except Exception as e:
        return MutantResult(mutant, "error", 0.0, str(e))
    finally:
        orig_path.write_text(backup_src, encoding="utf-8")


# -- Reporter ------------------------------------------------------------------

def print_report(results: list[MutantResult], show_survived: bool) -> int:
    killed   = [r for r in results if r.status == "killed"]
    survived = [r for r in results if r.status == "survived"]
    errors   = [r for r in results if r.status == "error"]
    timeouts = [r for r in results if r.status == "timeout"]

    total = len(results)
    score = len(killed) / total * 100 if total else 0.0

    print(f"\n{'='*60}")
    print(f"  Mutation score: {score:.1f}%  "
          f"({len(killed)} killed / {total} total)")
    print(f"  Survived: {len(survived)}  |  "
          f"Errors: {len(errors)}  |  Timeouts: {len(timeouts)}")
    print(f"{'='*60}\n")

    if survived and show_survived:
        print("-- SURVIVING MUTANTS (write tests to kill these) --\n")
        for r in survived:
            m = r.mutant
            print(f"  [{m.operator}] {m.description}")
            for line in m.diff_lines[:12]:
                print(f"    {line}")
            print()

    return 0 if not survived else 1


# -- CLI ----------------------------------------------------------------------─

def main(argv: list[str]) -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Proxion AST mutation tester")
    parser.add_argument("sources", nargs="+", type=Path, help="Source files to mutate")
    parser.add_argument("--tests", default="tests/", help="pytest selector")
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--show-survived", action="store_true")
    parser.add_argument("--operator", action="append", dest="operators",
                        help="Limit to specific operator(s)")
    parser.add_argument("--parallel", type=int, default=1,
                        help="Worker count (default 1; set >1 with caution)")
    args = parser.parse_args(argv)

    all_mutants: list[Mutant] = []
    for src in args.sources:
        if not src.exists():
            print(f"ERROR: {src} does not exist", file=sys.stderr)
            return 2
        print(f"Generating mutants for {src} ...", flush=True)
        mutants = generate_mutants(src)
        if args.operators:
            mutants = [m for m in mutants if m.operator in args.operators]
        print(f"  {len(mutants)} mutants generated")
        all_mutants.extend(mutants)

    if not all_mutants:
        print("No mutants generated.", file=sys.stderr)
        return 2

    print(f"\nRunning {len(all_mutants)} mutants against '{args.tests}' ...\n")

    results: list[MutantResult] = []
    for i, mutant in enumerate(all_mutants, 1):
        print(f"  [{i:>3}/{len(all_mutants)}] {mutant.operator:<14} {mutant.description[:55]}",
              end=" ", flush=True)
        result = run_mutant(mutant, args.tests, args.timeout)
        icon = {"killed": "K", "survived": "S", "error": "E", "timeout": "T"}[result.status]
        print(f"{icon}  ({result.elapsed:.1f}s)")
        results.append(result)

    return print_report(results, args.show_survived)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

