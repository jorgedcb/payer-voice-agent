"""Save the native LiveKit report; the backend owns consumption and extraction."""

import asyncio
import json
import logging
import time
from contextlib import closing
from os import getenv
from urllib.parse import quote

import boto3
from botocore.config import Config
from livekit.agents import JobContext

logger = logging.getLogger(__name__)

# The one key this worker adds to the native report. LiveKit emits no webhook when
# the payer answers: the SIP leg joins the room while still dialing and its
# later switch to "active" is only an attribute update. Only this worker sees
# the answer, when create_sip_participant returns, so it stamps the moment here
# and the backend reads it to date the call's start.
ANSWERED_AT_KEY = "agent_answered_at"
# The closing the interview confirmed on the line: the representative's name,
# the call reference (or the statement that none exists). Stamped by
# InterviewAgent.end_call; the backend can trust it over a transcript read.
CLOSING_KEY = "agent_closing"


def mark_answered(ctx: JobContext) -> None:
    """Record that the payer just answered this job's call.

    Kept on the job context so it lives exactly as long as the job: nothing
    to key by room and nothing to clean up if the job never reaches its
    session-end hook.
    """
    setattr(ctx, "answered_at", time.time())


def _upload_report(bucket: str, key: str, body: bytes) -> None:
    endpoint = getenv("S3_ENDPOINT_URL") or None
    # Use the SDK credential chain and retry policy, without a second retry loop.
    with closing(
        boto3.client(
            "s3",
            endpoint_url=endpoint,
            config=Config(
                s3={"addressing_style": "path" if endpoint else "auto"},
                connect_timeout=3,
                read_timeout=5,
                retries={"mode": "standard", "total_max_attempts": 3},
            ),
        )
    ) as client:
        client.put_object(
            Bucket=bucket,
            Key=key,
            Body=body,
            ContentType="application/json",
        )


async def on_session_end(ctx: JobContext) -> None:
    if ctx.is_fake_job():
        return
    bucket = getenv("SESSION_REPORT_BUCKET", "")
    if not bucket:
        logger.info("SESSION_REPORT_BUCKET not configured; report upload disabled")
        return

    # The SDK invokes this hook after finalizing the voice pipeline. No dispatch
    # fields or derived transcript are added to the native artifact; the answer
    # time is the single namespaced addition, and only for an answered call.
    answered_at = getattr(ctx, "answered_at", None)
    # Named apart from `contextlib.closing`, which `_upload_report` uses.
    closing_stamp = getattr(ctx, "closing", None)
    try:
        report = ctx.make_session_report().to_dict()
        if answered_at is not None:
            report[ANSWERED_AT_KEY] = answered_at
        if closing_stamp is not None:
            report[CLOSING_KEY] = closing_stamp
        body = json.dumps(report, ensure_ascii=False).encode("utf-8")
        key = f"calls/{quote(ctx.room.name, safe='')}/report.json"
        # Boto3 is synchronous; keep storage I/O off the voice worker's event loop.
        await asyncio.to_thread(_upload_report, bucket, key, body)
    except Exception:
        # Report bodies and SDK exception details may contain sensitive data.
        logger.error("Failed to save session report")
