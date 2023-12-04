"""
Keys used in Redis. This tries to be a single point of storing all the potential
variables etc.

NOTE: Lua scripts do not use this file, so if you change something, make sure you
change it also in the lua scripts.
"""

class Keys:
    @staticmethod
    def base() -> str:
        return "lde"

class ReservationKeys:

    class parameters:
        status = 'status'
        metadata = 'metadata'
        laboratory = 'laboratory'

    class states:
        pending = 'pending'
        broken = 'broken'
        unavailable = 'unavailable'
        cancelling = 'cancelling'
        queued = 'queued'
        initializing = 'initializing'
        ready = 'ready'
        finishing = 'finishing'
        finished = 'finished'

        finished_states = [finished, broken, unavailable]

    def __init__(self, reservation_id):
        self.reservation_id = reservation_id

    def base(self) -> str:
        return f"{Keys.base()}:reservations:{self.reservation_id}"

class ResourceKeys:
    def __init__(self, resource_id):
        self.resource_id = resource_id
    
    def channel(self) -> str:
        return f"{self.base()}:channel"
    
    def assigned(self) -> str:
        return f"{self.base()}:assigned"
    
    def base(self) -> str:
        return f"{Keys.base()}:resources:{self.resource_id}"