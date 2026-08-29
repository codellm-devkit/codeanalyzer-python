"""Config-use e2e fixture for all six detection shapes (#162 Task 5, #165)."""
import os


# Shape 1: Direct literal, reads DATABASE_URL from .env (resolves at -a 2)
def get_database_url():
    return os.getenv("DATABASE_URL")


# Shape 2: Variable-closing to literal, reads API_KEY from .env
# Resolves only at -a 3+ (DDG single-literal closure)
def get_api_key():
    key_name = "API_KEY"
    return os.getenv(key_name)


# Shape 3: Param-passed via local helper, reads SECRET_API_TOKEN from .env
# Resolves only at -a 4 (interprocedural parameter-passing)
def _read_config(name):
    return os.getenv(name)


def get_secret_token():
    return _read_config("SECRET_API_TOKEN")


# Shape 4: Multi-def unresolved (two different literals on branches)
# Should stay unresolved at all levels (reason: "non-literal")
def get_config_multi_def(use_debug):
    if use_debug:
        var = "DEBUG"
    else:
        var = "FLASK_ENV"
    return os.getenv(var)


# Shape 5: Undefined-key read
# Should appear in config_reads_unresolved with reason: "undefined-key"
def get_missing_config():
    return os.getenv("NOT_DEFINED_ANYWHERE")


# Shape 6: Deployment-env, reads APP_MODE from the Dockerfile ENV directive
# (#165). Direct literal, resolves at -a 2 like shape 1.
def get_app_mode():
    return os.getenv("APP_MODE")
