from pydantic import BaseModel


class ErrorResponse(BaseModel):
    code: str
    detail: str
    request_id: str | None = None
