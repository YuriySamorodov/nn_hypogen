from src.settings import Settings


def qdrant_connection_kwargs(settings: Settings) -> dict[str, str]:
    if settings.qdrant_url == ":memory:":
        return {"location": ":memory:"}
    return {"url": settings.qdrant_url}

