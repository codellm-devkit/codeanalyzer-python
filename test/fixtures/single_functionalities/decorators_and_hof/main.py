"""Decorator and higher-order function patterns.

Exercises:
- Simple function wrapper (functools.wraps)
- Parameterised decorator factory
- Class-based decorator (__call__)
- Higher-order function (function passed as argument)
- Closure / function factory
- Decorator stacking
"""
import functools


# ---------------------------------------------------------------------------
# 1. Simple wrapper decorator
# ---------------------------------------------------------------------------

def log_call(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        result = func(*args, **kwargs)
        return result
    return wrapper


# ---------------------------------------------------------------------------
# 2. Parameterised decorator factory
# ---------------------------------------------------------------------------

def repeat(n: int):
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            result = None
            for _ in range(n):
                result = func(*args, **kwargs)
            return result
        return wrapper
    return decorator


# ---------------------------------------------------------------------------
# 3. Class-based decorator
# ---------------------------------------------------------------------------

class Timer:
    def __init__(self, func):
        functools.update_wrapper(self, func)
        self.func = func
        self.call_count = 0

    def __call__(self, *args, **kwargs):
        self.call_count += 1
        return self.func(*args, **kwargs)


# ---------------------------------------------------------------------------
# 4. Higher-order functions
# ---------------------------------------------------------------------------

def apply(func, value):
    """Call *func* with *value* and return the result."""
    return func(value)


def double(x):
    return x * 2


def triple(x):
    return x * 3


def compose(f, g):
    """Return a new function h(x) = f(g(x))."""
    def h(x):
        return f(g(x))
    return h


# ---------------------------------------------------------------------------
# 5. Closure / function factory
# ---------------------------------------------------------------------------

def make_adder(n: int):
    def adder(x):
        return x + n
    return adder


def make_multiplier(n: int):
    def multiplier(x):
        return x * n
    return multiplier


# ---------------------------------------------------------------------------
# 6. Decorated callables
# ---------------------------------------------------------------------------

@log_call
def greet(name: str) -> str:
    return f"Hello, {name}"


@repeat(3)
def say_hello():
    print("hello")


@Timer
def compute(x, y):
    return x + y


@log_call
@repeat(2)
def stacked(value):
    return value * 10


# ---------------------------------------------------------------------------
# 7. Driver
# ---------------------------------------------------------------------------

def main():
    r1 = apply(double, 10)
    r2 = apply(triple, 10)

    double_then_triple = compose(triple, double)
    r3 = double_then_triple(5)

    add5 = make_adder(5)
    mul3 = make_multiplier(3)
    r4 = add5(10)
    r5 = mul3(10)

    r6 = greet("world")
    say_hello()
    r7 = compute(2, 3)
    r8 = stacked(7)

    return r1, r2, r3, r4, r5, r6, r7, r8


if __name__ == "__main__":
    main()
