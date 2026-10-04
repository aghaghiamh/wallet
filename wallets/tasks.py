import logging

from celery import shared_task
from kombu.exceptions import OperationalError

from wallets.models import TopUp
from wallets.topups import process_top_up

logger = logging.getLogger(__name__)


@shared_task(name="wallets.process_top_up", acks_late=True, ignore_result=True)
def process_top_up_task(top_up_id):
    return process_top_up(top_up_id)


@shared_task(name="wallets.dispatch_pending_top_ups", ignore_result=True)
def dispatch_pending_top_ups():
    dispatched = 0
    ids = TopUp.objects.filter(status=TopUp.Status.PENDING).values_list("id", flat=True)
    for top_up_id in ids.iterator(chunk_size=500):
        try:
            process_top_up_task.apply_async(args=[str(top_up_id)], expires=30, retry=False)
        except OperationalError:
            logger.exception("Broker unavailable; pending intents will be rediscovered")
            break
        dispatched += 1
    return dispatched
