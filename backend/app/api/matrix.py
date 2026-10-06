from functools import lru_cache
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..core.config import get_settings
from ..core.errors import RouterError, RouterTooManyPointsError, ValidationError
from ..domain.models import Coordinate
from ..domain.routing import MatrixPoint
from ..providers.routing.osrm import OsrmRouter
from ..services.matrix_service import MatrixProblem, MatrixService

router = APIRouter()


class MatrixPointRequest(BaseModel):
    # The client's own identifier, echoed back unchanged so the UI can map
    # matrix rows back to its rows. The first point is the start point.
    id: str = Field(..., min_length=1)
    lat: float
    lon: float


class MatrixProblemModel(BaseModel):
    id: str
    kind: str
    message: str


class MatrixResponse(BaseModel):
    # Point ids in matrix order; the start point is first.
    ids: List[str]
    # Rounded to whole seconds / metres. A null cell means no route between
    # that pair.
    durations_s: List[List[Optional[int]]]
    distances_m: List[List[Optional[int]]]
    problems: List[MatrixProblemModel]
    provider: str
    profile: str
    note: str


@lru_cache(maxsize=1)
def _shared_router() -> OsrmRouter:
    """One provider instance for the whole process.

    The rate limiter and the response cache live on the instance, so building a
    new one per request would reset both.
    """
    return OsrmRouter(get_settings())


def get_router() -> OsrmRouter:
    return _shared_router()


def get_matrix_service(
    router: OsrmRouter = Depends(get_router),
) -> MatrixService:
    settings = get_settings()
    return MatrixService(router, max_points=settings.MATRIX_MAX_POINTS)


def _round_matrix(
    matrix: List[List[Optional[float]]],
) -> List[List[Optional[int]]]:
    return [
        [None if value is None else int(round(value)) for value in row]
        for row in matrix
    ]


@router.post("/matrix", response_model=MatrixResponse)
async def compute_matrix(
    req: List[MatrixPointRequest],
    service: MatrixService = Depends(get_matrix_service),
):
    points = [
        MatrixPoint(id=item.id, coordinate=Coordinate(item.lat, item.lon))
        for item in req
    ]
    try:
        result = await service.compute(points)
    except RouterTooManyPointsError:
        raise HTTPException(
            status_code=400,
            detail="Too many points for the routing service. Reduce the list and try again.",
        )
    except RouterError as e:
        raise HTTPException(
            status_code=502,
            detail={"message": str(e), "error_kind": e.kind},
        )
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))

    matrix = result.matrix
    return MatrixResponse(
        ids=result.ids,
        durations_s=_round_matrix(matrix.durations_s),
        distances_m=_round_matrix(matrix.distances_m),
        problems=[
            MatrixProblemModel(id=p.id, kind=p.kind, message=p.message)
            for p in result.problems
        ],
        provider=matrix.provider,
        profile=matrix.profile,
        note="Durations are typical driving times without live traffic.",
    )