import unittest
from unittest import mock

from labdiscoveryengine import create_app
from labdiscoveryengine.scheduling.data import ReservationStatus

class UserTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app('testing')
        self.app_context = self.app.app_context()
        self.app_context.push()
        self.client = self.app.test_client()

    def tearDown(self):
        self.app_context.pop()


    def test_login(self):
        self.client.get('/login')

    def _login_as_admin(self):
        with self.client.session_transaction() as session:
            session['username'] = 'admin'
            session['role'] = 'admin'
            session['is_db'] = False

    def test_resource_access_is_rendered_as_link(self):
        self._login_as_admin()

        response = self.client.get('/user/')

        self.assertEqual(200, response.status_code)
        html = response.get_data(as_text=True)
        self.assertIn('/user/launch?laboratory=dummy&amp;group=All+laboratories&amp;resource=fpga-1', html)
        self.assertIn('class="btn btn-secondary btn-sm resource-access-btn"', html)

    def test_lab_without_image_does_not_render_broken_image(self):
        self._login_as_admin()
        self.app.config['INSTITUTION_LOGO'] = ''

        response = self.client.get('/user/')

        self.assertEqual(200, response.status_code)
        html = response.get_data(as_text=True)
        self.assertNotIn('src=""', html)

    def test_public_page_without_images_does_not_render_broken_image(self):
        self.app.config['INSTITUTION_LOGO'] = ''

        response = self.client.get('/public/')

        self.assertEqual(200, response.status_code)
        html = response.get_data(as_text=True)
        self.assertNotIn('src=""', html)

    @mock.patch("labdiscoveryengine.views.user.add_reservation")
    def test_create_reservation_adds_back_variants_to_client_initial_data(self, add_reservation):
        self._login_as_admin()
        add_reservation.return_value = ReservationStatus(
            status="queued",
            reservation_id="reservation-1",
            position=0,
        )

        response = self.client.post(
            '/user/api/reservations/',
            json={
                'laboratory': 'dummy',
                'group': 'All laboratories',
                'resources': ['fpga-1'],
            },
        )

        self.assertEqual(200, response.status_code)
        reservation_request = add_reservation.call_args.kwargs['reservation_request']
        self.assertEqual(reservation_request.back_url, reservation_request.client_initial_data['back'])
        self.assertEqual(reservation_request.back_url, reservation_request.client_initial_data['back_url'])
        self.assertEqual(reservation_request.back_url, reservation_request.client_initial_data['backUrl'])
