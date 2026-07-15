"""Module-global fixture: written in one function, read in another."""

counter = 0


def bump(amount):
    global counter
    counter = counter + amount


def read_counter():
    return counter
