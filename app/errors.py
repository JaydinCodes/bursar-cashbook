from datetime import datetime
from uuid import uuid4


def new_error_id() -> str:
    return f"ERR-{datetime.now().astimezone():%Y%m%d}-{uuid4().hex[:8].upper()}"
