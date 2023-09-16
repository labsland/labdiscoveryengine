import os
from flask import Flask, has_request_context, request, session, current_app
from werkzeug.security import generate_password_hash

import yaml
from flask_babel import Babel
from flask_assets import Environment, Bundle

try:
    from flask_debugtoolbar import DebugToolbarExtension
except ImportError:
    DebugToolbarExtension = None

babel = Babel()
environment = Environment()
if DebugToolbarExtension is not None:
    toolbar = DebugToolbarExtension()
else:
    toolbar = None

def create_app(config_name: str = 'default'):

    app = Flask(__name__)

    # Load the configuration from configuration.yml BEFORE importing the
    # configuration.variables module, so it uses os.environ which can be
    # precisely the configuration.yml file as defaults.
    if os.path.exists('configuration.yml'):
        configuration = yaml.safe_load(open('configuration.yml'))
        for configuration_key, configuration_value in configuration.items():
            os.environ[configuration_key] = str(configuration_value)

    from .configuration.variables import configurations
    app.config.from_object(configurations[config_name])

    from .configuration.storage import get_latest_configuration
    with app.app_context():
        latest_configuration = get_latest_configuration()

    app.config['LDE_CONFIG'] = latest_configuration

    babel.init_app(app, locale_selector=get_locale)
    environment.init_app(app)

    if toolbar is not None:
        toolbar.init_app(app)

    from .bundles import register_bundles
    register_bundles(environment)

    from .views.user import user_blueprint
    from .views.login import login_blueprint

    app.register_blueprint(login_blueprint)
    app.register_blueprint(user_blueprint, url_prefix='/user')
    
    return app

SUPPORTED_TRANSLATIONS = None

def get_locale():
    """ Defines what's the current language for the user. It uses different approaches. """
    # 'en' is supported by default
    global SUPPORTED_TRANSLATIONS
    if SUPPORTED_TRANSLATIONS is None:
        supported_languages = ['en']
        for translation in babel.list_translations():
            if translation.territory:
                iter_language = '{}_{}'.format(translation.language, translation.territory)
            else:
                iter_language = translation.language
            if iter_language not in supported_languages:
                supported_languages.append(iter_language)

        SUPPORTED_TRANSLATIONS = supported_languages
    else:
        supported_languages = SUPPORTED_TRANSLATIONS

    locale = None

    # This is used also from tasks (which are not in a context environment)
    if has_request_context():
        # If user accesses ?locale=es force it to Spanish, for example
        locale = request.args.get('locale', None)
        if locale not in supported_languages:
            locale = None

    # Otherwise, check what the web browser is using (the web browser might state multiple
    # languages)
    if has_request_context():
        if locale is None:
            if session.get('locale') is not None:
                locale = session['locale']

        if locale is None:
            locale = request.accept_languages.best_match(supported_languages)

    # Otherwise... use the default one (English)
    if locale is None:
        locale = 'en'

    session['locale'] = locale

    return locale
