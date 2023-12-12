import signal
import asyncio
import logging

from typing import Dict

logger = logging.getLogger(__name__)

from labdiscoveryengine.scheduling.asyncio.resource_worker import ResourceWorker, initialize_worker
from labdiscoveryengine.scheduling.asyncio.redis import is_redis_flushed

from labdiscoveryengine.utils import lde_config
import time

class WorkerAggregator:
    def __init__(self):
        self.resource_workers: Dict[str, ResourceWorker] = {
            # resource: task
        }
        self.stopping = False
        self.stopped = True
        self.task = None

    async def run(self):
        """
        Run adding new workers if needed or removing the ones that are not present anymore
        """
        self.stopped = False
        try:
            while not self.stopping:
                for resource in lde_config.resources:
                    if resource not in self.resource_workers:
                        self.resource_workers[resource] = ResourceWorker(resource)
                        await self.resource_workers[resource].start()
                    
                    elif not self.resource_workers[resource].running() and not self.stopping:
                        logger.info(f"Resource {resource} was stopped. Restarting it...")
                        await self.resource_workers[resource].start()

                for resource in self.resource_workers:
                    if resource not in lde_config.resources:
                        await self.resource_workers[resource].stop()
                        del self.resource_workers[resource]

                flushed = await is_redis_flushed()
                if flushed:
                    logger.error("Redis has been flushed. It might have been restarted, but the database now is inconsistent. Stopping worker")
                    self.stop()
                    break

                await asyncio.sleep(1)

        except asyncio.CancelledError:
            pass

        finally:
            logger.info("Stopping every worker...")

            for resource in self.resource_workers:
                await self.resource_workers[resource].stop()

            self.stopped = True

    def start(self):
        self.task = asyncio.create_task(self.run())
        return self.task

    def stop(self):
        self.stopping = True
        self.task.cancel()

aggregator = WorkerAggregator()

async def main():
    """
    """
    asyncio.get_event_loop().add_signal_handler(signal.SIGINT, lambda : signal_handler(signal.SIGINT))
    asyncio.get_event_loop().add_signal_handler(signal.SIGTERM, lambda : signal_handler(signal.SIGTERM))

    logger.info("WorkerAggregator running forever...")

    await initialize_worker()

    global aggregator
    await aggregator.start()

    logger.info(f"WorkerAggregator stopped...")

def signal_handler(signal):
    logger.info(f"Received signal {signal}, requesting stop")
    aggregator.stop()