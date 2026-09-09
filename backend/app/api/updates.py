from typing import Any

from fastapi import APIRouter, HTTPException

from app.services import updates

router = APIRouter(prefix="/updates", tags=["updates"])


@router.get("")
def status() -> dict[str, Any]:
    return updates.cached_status()


@router.post("/check")
def check() -> dict[str, Any]:
    return updates.check(force=True)


@router.post("/install")
def install() -> dict[str, Any]:
    try:
        return updates.install()
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
