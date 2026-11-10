from fantracker.config import Settings
from fantracker.sources.base import FanSource, SourceError
from fantracker.sources.umamoe import UmaMoeClient


def build_source(settings: Settings) -> FanSource:
    """Create the uma.moe client. Needs UMAMOE_API_KEY."""
    if not settings.umamoe_api_key:
        raise SourceError("UMAMOE_API_KEY is not set, so club data cannot be fetched.")
    return UmaMoeClient(settings.umamoe_api_key)


__all__ = ["FanSource", "SourceError", "UmaMoeClient", "build_source"]
