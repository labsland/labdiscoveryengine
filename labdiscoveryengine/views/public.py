from flask import Blueprint, current_app
from labdiscoveryengine.utils import lde_config
from labdiscoveryengine.views.utils import render_themed_template

public_blueprint = Blueprint('public', __name__)

@public_blueprint.route('/')
def index():
    """
    This is the public index page.
    """
    if current_app.config['DEFAULT_LAB_VISIBILITY'] == 'private':
        return "The laboratories are not public.", 403
    return render_themed_template('public/index.html', laboratories=lde_config.laboratories.values())
