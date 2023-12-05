# Store all the script names
from typing import Dict


class ScriptNames:
    assign_reservation_to_resource = 'assign_reservation_to_resource'
    store_reservation = 'store_reservation'
    get_reservation_status = 'get_reservation_status'

# Store all the scripts here
SCRIPT_FILES: Dict[str, str] = {
    ScriptNames.assign_reservation_to_resource: 'lua/assign_reservation_to_resource.lua',
    ScriptNames.store_reservation: 'lua/store_reservation.lua',
    ScriptNames.get_reservation_status: 'lua/get_reservation_status.lua'
}