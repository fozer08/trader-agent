from datetime import time
from typing import Annotated

from pydantic import BeforeValidator


def _parse_time(value: object) -> time:
    if isinstance(value, time):
        result = value
    elif isinstance(value, str):
        parts = value.split(":")
        if len(parts) not in (2, 3):
            raise ValueError("Saat HH:MM veya HH:MM:SS olmalıdır.")
        result = time(int(parts[0]), int(parts[1]), int(parts[2]) if len(parts) == 3 else 0)
    else:
        raise ValueError("Saat HH:MM veya HH:MM:SS olmalıdır.")
    if result.tzinfo is not None:
        raise ValueError("Saat timezone içermemelidir.")
    if result.microsecond:
        raise ValueError("Seans saatlerinde saniyeden küçük çözünürlük desteklenmez.")
    return result


TimeField = Annotated[time, BeforeValidator(_parse_time)]
