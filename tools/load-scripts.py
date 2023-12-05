import time
import pprint
import requests

def main():
    base_url = "http://localhost:5000"
    external_username = 'labsland'
    external_password = 'password'

    auth = (external_username, external_password)
    sess = requests.Session()

    r = sess.post(f"{base_url}/external/v1/reservations/",
            auth=auth,
            json={
                'laboratory': 'dummy',
                'userIdentifier': 'john',
                'locale': 'en',
                'backUrl': 'https://labdiscoveryengine.labsland.com',
            }, 
    )

    if r.status_code != 200:
        print(r.content)
        return

    result = r.json()
    reservation_id = result['reservation_id']
    previous_status = result['status']
    previous_position = result.get('position')
    print(time.asctime())
    pprint.pprint(result)

    if not result.get('success'):
        return

    while True:
        parameters = f'?previous_status={previous_status}&max_time=8'
        if previous_position is not None:
            parameters += f'&previous_position={previous_position}'
        print(time.asctime(), "before request...")
        r = sess.get(f"{base_url}/external/v1/reservations/{reservation_id}" + parameters, auth=auth)
        r.raise_for_status()
        result = r.json()
        print(time.asctime(), "...after request")
        previous_status = result['status']
        previous_position = result.get('position')
        pprint.pprint(result)
        if result['status'] not in ('queued', 'initializing', 'ready'):
            break


if __name__ == '__main__':
    main()
