import pprint
import requests

base_url = "http://localhost:5000"
external_username = 'labsland'
external_password = 'password'

r = requests.post(f"{base_url}/external/v1/reservations/", 
        auth=(external_username, external_password),
        json={
            'laboratory': 'dummy',
            'userIdentifier': 'john'
        }, 
)

if r.status_code == 200:
    pprint.pprint(r.json())
else:
    print(r.content)
