import secrets
from typing import List, Optional
from flask import Blueprint, jsonify, g, request
from labdiscoveryengine.scheduling.data import ReservationRequest
from labdiscoveryengine.scheduling.web_api import add_reservation

from labdiscoveryengine.utils import lde_config

external_v1_blueprint = Blueprint('external', __name__)

@external_v1_blueprint.before_request
def before_request():
    # Check authentication
    if request.authorization is None:
        return jsonify(success=False, message='Unauthorized'), 401, {'WWW-Authenticate': 'Basic realm="Login Required"'}
    
    external_username = request.authorization.username
    external_password = request.authorization.password

    if external_username not in lde_config.external_users:
        return jsonify(success=False, message='Unauthorized'), 401, {'WWW-Authenticate': 'Basic realm="Login Required"'}
    
    if not lde_config.external_users[external_username].check_password_hash(external_password):
        return jsonify(success=False, message='Unauthorized'), 401, {'WWW-Authenticate': 'Basic realm="Login Required"'}
    
    g.external_username = external_username    

@external_v1_blueprint.route('/')
def index():
    return jsonify(success=True, message=f'Hi {g.external_username}!')

@external_v1_blueprint.route('/reservations/', methods=['GET', 'POST'])
def reservations():
    """
    Either get the current list of reservations (for this external user) or add a new reservation.

    If GET, it will receive a simple list:
    {
        "reservations": ["reservation1", "reservation2", "reservation3"]
    }

    If POST, it will expect:
    {
        'laboratory': 'dummy',
        'resources': ['dummy-1'], # optional, if the user wants a particular resource or any of the resources
        'features': ['feature1', 'feature2'], #  optional, if the user wants a subset of the laboratory defined by features
        'userIdentifier': 'asdfafadfa' # anonymous user identifier of the user in the third-party system
    }
    """
    if request.method == 'POST':
        request_data = request.get_json(force=True, silent=True) or {}
        laboratory: Optional[str] = request_data.get('laboratory')
        if not laboratory:
            return jsonify(success=False, code='invalid-request', message='Missing laboratory'), 400
        
        if laboratory not in lde_config.laboratories:
            # This would usually be a security issue, as external users will know the full list of laboratories (secret or not)
            # However, in 99% of the cases, the LDE host trusts the external system, and it can help debugging distributed systems
            return jsonify(success=False, code='invalid-request', message='Laboratory {laboratory} does not exist'), 400
        
        resources: Optional[str] = request_data.get('resources') or [] # Ok if empty

        for resource in resources:
            if not isinstance(resource, str):
                return jsonify(success=False, code='invalid-request', message=f'Invalid resource (must be string): {resource}'), 400
            
        user_identifier = request_data.get('userIdentifier')
        if not user_identifier:
            return jsonify(success=False, code='invalid-request', message='Missing userIdentifier'), 400

        features: List[str] = request_data.get('features') or []
        if not isinstance(features, list):
            return jsonify(success=False, code='invalid-request', message='Invalid features (must be list)'), 400
        
        for feature in features:
            if not isinstance(feature, str):
                return jsonify(success=False, code='invalid-request', message=f'Invalid feature (must be string): {feature}'), 400

        if laboratory not in lde_config.external_users[g.external_username].laboratories:
            return jsonify(success=False, code='invalid-request', message='User {g.external_username} is not authorized to reserve in {laboratory}'), 400

        reservation_request = ReservationRequest(
            identifier=secrets.token_urlsafe(),
            laboratory=laboratory,
            resources=resources,
            feature=features,
            external_user_identifier=user_identifier,
            user_identifier=g.external_username, 
            user_role='external'
        )

        add_reservation(reservation_request=reservation_request)

        return jsonify(success=True, message='Reservation added', identifier=reservation_request.identifier)
    
    return jsonify(success=True, message='Not implemented')