"""
Methods that should be called from the web interface (not running in asyncio, and not using asyncio libraries)
"""

from typing import List

from .data import ReservationRequest

def add_reservation(reservation_request: ReservationRequest):
    pass

def get_reservation_list(user_identifier: str, user_role: str) -> List[str]:
    pass
