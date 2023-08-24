from flask import Blueprint

from labdiscoveryengine.views.utils import render_themed_template

login_blueprint = Blueprint('login', __name__)

@login_blueprint.route('/login')
def login():
    """
    This is the login page.
    """
    return render_themed_template('login.html')