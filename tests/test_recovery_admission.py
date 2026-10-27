import unittest
from unittest.mock import patch
from flask import Flask
from tests.test_lab_recovery import pkg
from recovery_under_test.admission import install_backend_guard, Unavailable

class Admission(unittest.TestCase):
    def setUp(self):
        self.app=Flask(__name__)
        install_backend_guard(self.app,'/root-owned/state.json',{},'board')
        for route in ['/healthcheck','/api/program','/weblab/sessions/','/ldl/sessions/old-session/status']:
            self.app.add_url_rule(route,endpoint=route,view_func=lambda:'accepted',methods=['GET','POST'])
        self.client=self.app.test_client()
    def test_recovering_resource_rejects_student_and_admin_mutations(self):
        with patch('recovery_under_test.admission.snapshot',side_effect=Unavailable('resource-recovering')):
            for path in ['/api/program','/weblab/sessions/','/ldl/sessions/old-session/status']:
                self.assertEqual(self.client.post(path).status_code,503)
            self.assertEqual(self.client.get('/healthcheck').status_code,200)
    def test_only_current_epoch_can_create_or_touch_session(self):
        with patch('recovery_under_test.admission.snapshot',return_value='new'):
            for path in ['/weblab/sessions/','/ldl/sessions/old-session/status']:
                for headers in [{},{'X-Lab-Recovery-Epoch':'old'}]:
                    self.assertEqual(self.client.post(path,headers=headers).status_code,503)
                self.assertEqual(self.client.post(path,headers={'X-Lab-Recovery-Epoch':'new'}).status_code,200)
            # Student session authorization remains the backend's native contract.
            self.assertEqual(self.client.post('/api/program').status_code,200)
