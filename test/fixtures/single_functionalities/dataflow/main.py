"""Intraprocedural dataflow constructs with hand-computable graphs.

Every callable here is referenced by name in the level-3 gate tests
(test_dataflow_*.py); keep line numbers stable when editing.
"""

from pipeline import chain_a
from state import bump, read_counter


def branchy(n):
    if n > 0:
        x = n + 1
    else:
        x = -n
    return x


def looped(n):
    total = 0
    i = 0
    while i < n:
        total = total + i
        i = i + 1
    return total


def early_exit(n):
    if n < 0:
        return -1
    y = n * 2
    return y


def risky(n):
    if n < 0:
        raise ValueError("negative")
    return n


def handles(n):
    try:
        v = risky(n)
        ok = 1
    except ValueError:
        v = 0
        ok = 0
    finally:
        done = True
    return v + ok


def with_block(path):
    with open(path) as fh:
        data = fh.read()
    return data


def comprehend(items):
    squares = [i * i for i in items]
    i = "not-the-loop-var"
    return squares, i


def gen(n):
    k = 0
    while k < n:
        yield k
        k = k + 1


async def slow(x):
    return x + 1


async def fetch(x):
    y = await slow(x)
    return y


def short_circuit(a, b):
    c = a and b
    d = a or b
    return c, d


def infinite():
    while True:
        pass


def drive(n):
    r = chain_a(n)
    bump(r)
    return read_counter()
