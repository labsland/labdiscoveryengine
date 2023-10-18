from flask import Blueprint, session, redirect, url_for, g
from labdiscoveryengine.utils import lde_config
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
    return render_themed_template('user/index.html', laboratories=lde_config.laboratories.values())
