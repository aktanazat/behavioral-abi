"""Wire-shaped (camelCase, no-null) JSON dumps of MCP pydantic result models.

Mirrors `babi.collect.bundle`'s dump convention (`by_alias=True,
exclude_none=True, mode="json"`) so every evidence blob in this project uses
the same JSON shape for the same kind of object, without reaching into
another module's private helper.
"""

from __future__ import annotations

from pydantic import BaseModel

from babi.canon import Json


def dump_model(model: BaseModel) -> dict[str, Json]:
    return model.model_dump(by_alias=True, exclude_none=True, mode="json")


__all__ = ["dump_model"]
