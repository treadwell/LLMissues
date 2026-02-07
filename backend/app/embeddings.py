import json
import os
from datetime import datetime
from typing import Iterable

from openai import OpenAI

DEFAULT_EMBED_MODEL = "text-embedding-3-small"


def embed_texts(texts: Iterable[str], model: str | None = None) -> list[list[float]]:
    client = OpenAI()
    use_model = model or os.environ.get("OPENAI_EMBEDDING_MODEL") or DEFAULT_EMBED_MODEL
    inputs = [t if t is not None else "" for t in texts]
    response = client.embeddings.create(model=use_model, input=inputs)
    return [item.embedding for item in response.data]


def serialize_vector(vec: list[float]) -> str:
    return json.dumps(vec)


def deserialize_vector(payload: str) -> list[float]:
    return json.loads(payload)


def now_iso() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")
