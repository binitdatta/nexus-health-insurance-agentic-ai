"""
Flat-file audit logging. Every LLM call gets one JSON line here, in
addition to (not instead of) the llm_call_log row in MySQL — this is
the "flat log file and also MySQL DB table" requirement. Kept
deliberately simple (one dict -> one json.dumps line) so it's trivial
to tail, grep, or ship to a log aggregator later.
"""
import json


def log_llm_call(logger, *, level: str = "INFO", **fields) -> None:
    record = {"log_type": "llm_call", **fields}
    line = json.dumps(record, default=str)
    getattr(logger, level.lower())(line)


def log_error(logger, *, message: str, **fields) -> None:
    record = {"log_type": "error", "message": message, **fields}
    logger.error(json.dumps(record, default=str))
