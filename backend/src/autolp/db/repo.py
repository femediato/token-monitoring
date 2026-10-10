"""Small persistence helpers shared by collectors."""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from autolp.db.models import RawObservation
from autolp.providers.http import ProviderResponse, ResponseSink


def raw_observation_from(
    resp: ProviderResponse, collector_run_id: int | None = None
) -> RawObservation:
    return RawObservation(
        fetched_at=resp.fetched_at,
        source=resp.source,
        endpoint=resp.endpoint,
        params=resp.params,
        http_status=resp.status_code,
        payload=resp.payload,
        payload_sha256=resp.payload_sha256,
        collector_run_id=collector_run_id,
    )


def raw_observation_sink(factory: async_sessionmaker[AsyncSession]) -> ResponseSink:
    """Sink that commits each provider response in its own transaction."""

    async def sink(resp: ProviderResponse) -> None:
        async with factory() as session, session.begin():
            session.add(raw_observation_from(resp))

    return sink
