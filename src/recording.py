"""Ask LiveKit Egress to save room audio directly to S3."""

import asyncio
import logging
from os import getenv
from urllib.parse import quote

from livekit import api
from livekit.agents import JobContext

logger = logging.getLogger(__name__)


async def start_recording(ctx: JobContext) -> None:
    if ctx.is_fake_job():
        return
    bucket = getenv('AUDIO_RECORDING_BUCKET', '')
    if not bucket:
        logger.info('AUDIO_RECORDING_BUCKET not configured; audio recording disabled')
        return
    access_key = getenv('AWS_ACCESS_KEY_ID', '')
    endpoint = getenv('S3_ENDPOINT_URL', '')
    secret = getenv('AWS_SECRET_ACCESS_KEY', '')
    if not access_key or not secret:
        logger.error('Audio recording requires AWS credentials for the Egress service')
        return

    try:
        # Do not retry an ambiguous start timeout: it could create two recorders.
        async with asyncio.timeout(15):
            info = await ctx.api.egress.start_room_composite_egress(
                api.RoomCompositeEgressRequest(
                    room_name=ctx.room.name,
                    audio_only=True,
                    file_outputs=[api.EncodedFileOutput(
                        file_type=api.EncodedFileType.OGG,
                        filepath=f"calls/{quote(ctx.room.name, safe='')}/audio.ogg",
                        s3=api.S3Upload(
                            bucket=bucket,
                            endpoint=endpoint,
                            force_path_style=bool(endpoint),
                            region=getenv('AWS_REGION') or getenv('AWS_DEFAULT_REGION', ''),
                            access_key=access_key,
                            secret=secret,
                            session_token=getenv('AWS_SESSION_TOKEN', ''),
                        ),
                    )],
                )
            )
        if not info.egress_id:
            raise RuntimeError('Egress response did not include a recording ID')
    except Exception:
        logger.error('Audio recording start failed; call will continue')
        return

    async def stop_recording() -> None:
        try:
            async with asyncio.timeout(25):
                await ctx.api.egress.stop_egress(
                    api.StopEgressRequest(egress_id=info.egress_id)
                )
        except Exception:
            # Room deletion may already have stopped Egress. The backend tracks audio
            # readiness from storage events independently of this stop request.
            logger.error('Audio recording stop request failed')

    ctx.add_shutdown_callback(stop_recording)
