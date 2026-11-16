import os

from dotenv import load_dotenv

from housing.errors import DataSourceError

load_dotenv()


def get_key(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise DataSourceError(f"{name} 키가 .env에 없어요")
    return value
