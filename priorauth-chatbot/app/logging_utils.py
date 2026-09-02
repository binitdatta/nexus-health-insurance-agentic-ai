import json


def log_http_call(logger, *, level: str = "INFO", **fields) -> None:
    logger_fn = getattr(logger, level.lower())
    logger_fn(json.dumps({"log_type": "http_call", **fields}, default=str))


def log_event(logger, *, level: str = "INFO", event: str, **fields) -> None:
    logger_fn = getattr(logger, level.lower())
    logger_fn(json.dumps({"log_type": event, **fields}, default=str))
