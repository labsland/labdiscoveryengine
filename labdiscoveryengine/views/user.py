from flask import Blueprint, session, redirect, url_for, g

from labdiscoveryengine.views.utils import render_themed_template

user_blueprint = Blueprint('user', __name__)

@user_blueprint.before_request
def before_request():
    # Check authentication
    username = session.get('username')
    role = session.get('role')
    if username is None or role is None:
        return redirect(url_for('login.login'))
    
    g.username = username
    g.role = role

@user_blueprint.route('/')
def index():
    """
    This is the user index page.
    """
    return render_themed_template('user/index.html')