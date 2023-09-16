import sys
import time
import asyncio

async def main():
    """
    """
    print(f"[{time.asctime()}] Running forever...", flush=True)

    # TODO: mechanism to stop, etc.
    while True:
        await asyncio.sleep(1)