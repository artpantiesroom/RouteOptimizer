from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..services.parse_service import ParseService

router = APIRouter()
parse_service = ParseService()


class ParseRequest(BaseModel):
    text: str = Field(default="")
    max_lines: int = Field(default=100, ge=1, le=200)
    max_line_length: int = Field(default=500, ge=1, le=1000)


class ParsedItem(BaseModel):
    index: int
    original: str
    trimmed: str
    is_blank: bool
    is_duplicate: bool
    duplicate_of: int | None = None


class ParseResponse(BaseModel):
    items: list[ParsedItem]
    total: int
    non_blank: int
    suggested_city: str | None = None


@router.post("/parse", response_model=ParseResponse)
async def parse(req: ParseRequest):
    try:
        parsed = parse_service.parse(req.text, max_lines=req.max_lines, max_line_length=req.max_line_length)
        return ParseResponse(
            items=[
                ParsedItem(
                    index=i.index,
                    original=i.original,
                    trimmed=i.trimmed,
                    is_blank=i.is_blank,
                    is_duplicate=i.is_duplicate,
                    duplicate_of=i.duplicate_of,
                )
                for i in parsed.items
            ],
            total=parsed.total,
            non_blank=parsed.non_blank,
            suggested_city=parsed.suggested_city,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
