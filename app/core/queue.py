"""RabbitMQ helpers — thin wrapper around pika.

RabbitMQ's only job is fair delivery to a free worker (DESIGN.md
"Exactly-once processing") — it is never consulted for ownership
decisions, so this module deliberately stays dumb: connect, declare,
publish. No retry-topology, no delayed-message plugin — retries and
scheduling are handled by the reconciler instead.
"""
import json

import pika

from app.core.config import get_settings

settings = get_settings()


def get_connection() -> pika.BlockingConnection:
    return pika.BlockingConnection(pika.URLParameters(settings.RABBITMQ_URL))


def declare_queue(channel) -> None:
    channel.queue_declare(queue=settings.QUEUE_NAME, durable=True)


def publish_job(channel, job_id: str) -> None:
    """Publish on an already-open channel — used by the worker/reconciler,
    which hold a channel open across many operations."""
    channel.basic_publish(
        exchange="",
        routing_key=settings.QUEUE_NAME,
        body=json.dumps({"job_id": job_id}).encode("utf-8"),
        properties=pika.BasicProperties(delivery_mode=2),  # persistent (durable queue + this = survives a broker restart)
    )


def publish_job_standalone(job_id: str) -> None:
    """Open a connection, publish once, close — used by the API layer,
    which shouldn't hold a broker connection open between requests."""
    connection = get_connection()
    try:
        channel = connection.channel()
        declare_queue(channel)
        publish_job(channel, job_id)
    finally:
        connection.close()
