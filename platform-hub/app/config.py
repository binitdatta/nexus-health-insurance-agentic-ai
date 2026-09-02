import os

from dotenv import load_dotenv


load_dotenv()


def _bool(name: str, default: str = "false") -> bool:
    return (
        os.getenv(name, default)
        .strip()
        .lower()
        in ("1", "true", "yes", "on")
    )


class Config:
    ENV = os.getenv(
        "FLASK_ENV",
        "production",
    )

    DEBUG = _bool(
        "FLASK_DEBUG",
        "false",
    )

    SECRET_KEY = os.getenv(
        "SECRET_KEY",
        "dev-only-not-security-sensitive-no-sessions-used",
    )

    HUB_HOST = os.getenv(
        "HUB_HOST",
        "localhost",
    )