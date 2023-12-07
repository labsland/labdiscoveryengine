# Store all the script names
from typing import Dict
import os


class ScriptNames:
    assign_reservation_to_resource = 'assign_reservation_to_resource'
    store_reservation = 'store_reservation'
    get_reservation_status = 'get_reservation_status'

_lde_directory = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Store all the scripts here
SCRIPT_FILES: Dict[str, str] = {
    ScriptNames.assign_reservation_to_resource: os.path.join(_lde_directory, 'lua/assign_reservation_to_resource.lua'),
    ScriptNames.store_reservation: os.path.join(_lde_directory, 'lua/store_reservation.lua'),
    ScriptNames.get_reservation_status: os.path.join(_lde_directory, 'lua/get_reservation_status.lua')
}