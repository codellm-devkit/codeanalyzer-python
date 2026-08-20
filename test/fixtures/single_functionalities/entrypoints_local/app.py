def route(path, methods=None):
    """Stands in for a framework's routing decorator."""
    def deco(fn):
        return fn
    return deco


@route("/products", methods=["POST"])
def create_product():
    return helper()


def helper():
    """Called only internally - must NOT be flagged."""
    return {}
