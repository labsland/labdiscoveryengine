from flask import Blueprint

from labdiscoveryengine.views.utils import render_themed_template

user_blueprint = Blueprint('user', __name__)

@user_blueprint.before_request
def before_request():
    # Check authentication
    pass

@user_blueprint.route('/')
def index():
    """
    This is the user index page.
    """
    return render_themed_template('user/index.html')