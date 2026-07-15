"""Interprocedural fixture: call chain, mutual recursion, aliasing, closures."""


def chain_a(v):
    return chain_b(v + 1)


def chain_b(v):
    return chain_c(v * 2)


def chain_c(v):
    return v - 3


def even(n):
    if n == 0:
        return True
    return odd(n - 1)


def odd(n):
    if n == 0:
        return False
    return even(n - 1)


class Box:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value


def alias_flow():
    p = Box(10)
    q = p
    q.value = 42
    return p.get()


def make_adder(base):
    def add(x):
        return x + base
    return add


def use_adder(n):
    add5 = make_adder(5)
    return add5(n)


def mutate(items):
    items.append(1)


def caller_of_mutate():
    xs = []
    mutate(xs)
    return xs
