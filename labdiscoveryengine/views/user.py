import secrets
from typing import List, Optional
from flask import Blueprint, jsonify, request, session, redirect, url_for, g

from sqlalchemy.orm import joinedload

from labdiscoveryengine import get_locale, db
from labdiscoveryengine.models import Group, User
from labdiscoveryengine.scheduling.data import ReservationRequest, ReservationStatus
from labdiscoveryengine.scheduling.sync.web_api import add_reservation, cancel_reservation, get_reservation_status
from labdiscoveryengine.utils import is_sql_active, lde_config
from labdiscoveryengine.views.login import LogoutForm
from labdiscoveryengine.views.utils import render_themed_template

user_blueprint = Blueprint('user', __name__)


@user_blueprint.before_request
def before_request():
    # Check authentication
    username = session.get('username')
    role = session.get('role')
    if username is None or role is None:
        return redirect(url_for('login.login'))

    logout_form = LogoutForm()
    
    g.username = username
    g.role = role
    g.logout_form = logout_form
    g.is_db = session.get('is_db', False)


@user_blueprint.context_processor
def inject_vars():
    logout_form = g.logout_form
    username = g.username
    return dict(logout_form=logout_form, username=username)


@user_blueprint.route('/')
def index():
    """
    This is the user index page.
    """
    groups = _get_user_groups_and_labs()   
    return render_themed_template('user/index.html', groups=groups, laboratories=lde_config.laboratories.values(), resources=lde_config.resources)

def _get_user_groups_and_labs():
    laboratories = lde_config.laboratories.values()

    groups = []
    if g.role == 'admin':
        groups.append({
            'name': 'All laboratories',
            'laboratories': laboratories,
            'laboratories_by_identifier': lde_config.laboratories
        })    
    
    if is_sql_active() and g.is_db:
        user = db.session.query(User).filter(User.login == g.username).options(joinedload(User.groups, Group.permissions)).first()
        if user is not None:
            user_groups = list(user.groups)
            user_groups.sort(key=lambda x: x.updated_at, reverse=True)
            for group in user_groups:
                group_laboratories = []
                for permission in group.permissions:
                    if permission.laboratory in lde_config.laboratories:
                        group_laboratories.append(lde_config.laboratories[permission.laboratory])

                groups.append({
                    'name': group.name,
                    'laboratories': group_laboratories,
                    'laboratories_by_identifier': {
                        lab.identifier: lab for lab in group_laboratories
                    },
                })
    return groups

@user_blueprint.route("/api/")
def api():
    return jsonify(success=True)

@user_blueprint.route('/api/reservations/', methods=['POST'])
def create_reservation():
    request_data = request.get_json(force=True, silent=True) or {}
    laboratory: Optional[str] = request_data.get('laboratory')
    if not laboratory:
        return jsonify(success=False, code='invalid-request', message='Missing laboratory'), 400
    
    if laboratory not in lde_config.laboratories:
        # This would usually be a security issue, as external users will know the full list of laboratories (secret or not)
        # However, in 99% of the cases, the LDE host trusts the external system, and it can help debugging distributed systems
        return jsonify(success=False, code='invalid-request', message='Laboratory {laboratory} does not exist'), 400
    
    user_groups = _get_user_groups_and_labs()
    laboratories_by_group = {group['name']: group['laboratories_by_identifier'] for group in user_groups}

    group = request_data.get('group')
    if group not in laboratories_by_group:
        return jsonify(success=False, code='invalid-request', message=f'Group {group} does not exist'), 400
    
    print(laboratories_by_group[group])
    if laboratory not in laboratories_by_group[group]:
        return jsonify(success=False, code='invalid-request', message=f'Laboratory {laboratory} is not in group {group}'), 400

    resources: Optional[str] = request_data.get('resources') or [] # Ok if empty

    for resource in resources:
        if not isinstance(resource, str):
            return jsonify(success=False, code='invalid-request', message=f'Invalid resource (must be string): {resource}'), 400
    
    if not resources:
        # If it adds no resources, it means that all resources are valid
        resources = list(lde_config.laboratories[laboratory].resources)

    features: List[str] = request_data.get('features') or []
    if not isinstance(features, list):
        return jsonify(success=False, code='invalid-request', message='Invalid features (must be list)'), 400
    
    for feature in features:
        if not isinstance(feature, str):
            return jsonify(success=False, code='invalid-request', message=f'Invalid feature (must be string): {feature}'), 400
        
    user_full_name = None
    if is_sql_active() and g.is_db:
        user = db.session.query(User).filter(User.login == g.username).first()
        if user is not None:
            user_full_name = user.full_name
    
    reservation_request = ReservationRequest(
        identifier=secrets.token_urlsafe(),
        group=group,
        laboratory=laboratory,
        resources=resources,
        features=features,
        user_identifier=g.username, 
        user_full_name=user_full_name,
        user_role=g.role,
        back_url=url_for(".index", _external=True) + f"#lab-{laboratory}",
        max_time=lde_config.laboratories[laboratory].max_time,
        locale=get_locale(),
    )

    reservation_status: ReservationStatus = add_reservation(reservation_request=reservation_request)

    return jsonify(success=True, message='Reservation added', **reservation_status.todict())


@user_blueprint.route('/api/reservations/<reservation_id>', methods=['GET'])
def reservation_get(reservation_id: str):
    previous_status = request.args.get("previous_status")
    previous_position = request.args.get("previous_position")
    if previous_position:
        try:
            previous_position = int(previous_position)
        except Exception as err:
            previous_position = None
    
    if previous_status:
        previous_reservation_status = ReservationStatus(status=previous_status, reservation_id=reservation_id, position=previous_position)
    else:
        previous_reservation_status = None

    # Max time is the maximum time the user is willing to wait if the state is the same
    # as the previous state. Otherwise, it does not affect the call and the result is returned
    # immediately
    default_max_time = 20
    try:
        max_time_waiting = float(request.args.get('max_time') or default_max_time)
    except:
        max_time_waiting = default_max_time

    max_time_waiting = min(max_time_waiting, default_max_time) # max_time cannot be higher than default_max_time

    reservation_status = get_reservation_status(g.username, reservation_id, previous_reservation_status=previous_reservation_status, max_time=max_time_waiting)
    if not reservation_status:
        return jsonify(success=False, message='Reservation not found'), 404

    return jsonify(success=True, **reservation_status.todict())

@user_blueprint.route('/api/reservations/<reservation_id>', methods=['DELETE'])
def reservation_delete(reservation_id: str):
    """
    Cancel the reservation
    """
    result = cancel_reservation(g.username, reservation_id)
    return jsonify(success=True, cancelled=result)
