from collections import OrderedDict
import os
import sys
from typing import Dict, Optional
from babel import Locale
from flask import Flask, has_request_context, request, session

import yaml
from flask_babel import Babel
from flask_assets import Environment
from flask_pymongo import PyMongo
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate

try:
    from flask_debugtoolbar import DebugToolbarExtension
except ImportError:
    DebugToolbarExtension = None

babel = Babel()
environment = Environment()

# We will not initialize mongo if we don't have mongo configured
mongo = PyMongo()
# We will not initialize mongo if we don't have SQLAlchemy configured
db = SQLAlchemy()
migrate = Migrate()

if DebugToolbarExtension is not None:
    toolbar = DebugToolbarExtension()
else:
    toolbar = None

def running_mode() -> str:
    """
    Return 'web' or 'worker' depending of it is running in web mode (e.g., 
    "flask run" or in gunicorn) or in worker mode (calling "flask worker run")
    """
    if os.environ.get('LDE_RUNNING_MODE') == 'worker':
        return 'worker'
    
    if len(sys.argv) < 2:
        return 'web'    
    
    for pos, arg in enumerate(sys.argv[:-1]):
        if arg == 'worker' and sys.argv[pos + 1] == 'run':
            return 'worker'
    
    return 'web'

from labdiscoveryengine.scheduling.sync.web_api import initialize_web

def create_app(config_name: Optional[str] = None):
    if config_name is None:
        config_name = 'default'

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

    if app.config.get('SQLALCHEMY_DATABASE_URI'):
        app.config['USING_SQLALCHEMY'] = True
        db.init_app(app)
        migrate.init_app(app, db, directory='labdiscoveryengine/migrations')
    else:
        app.config['USING_SQLALCHEMY'] = False

    if app.config.get('MONGO_URI'):
        app.config['USING_MONGO'] = True
        mongo.init_app(app)
        create_mongodb_indexes()
    else:
        app.config['USING_MONGO'] = False

    if toolbar is not None:
        toolbar.init_app(app)

    from .bundles import register_bundles
    register_bundles(environment)

    from .views.user import user_blueprint
    from .views.login import login_blueprint
    from .views.external import external_v1_blueprint
    from .views.public import public_blueprint
    from .views.admin import create_admin

    admin = create_admin(app)
    admin.init_app(app)

    app.register_blueprint(login_blueprint)
    app.register_blueprint(user_blueprint, url_prefix='/user')
    app.register_blueprint(external_v1_blueprint, url_prefix='/external/v1')
    app.register_blueprint(public_blueprint, url_prefix='/public')

    def _list_languages() -> Dict[str, str]:
        global SUPPORTED_LANGUAGES
        if SUPPORTED_LANGUAGES is None:
            SUPPORTED_LANGUAGES = OrderedDict()

            translations = babel.list_translations()
            for language in sorted(translations, key=lambda x: x.language):
                try:
                    display_name = language.get_display_name(language).title()
                except:
                    display_name = language
                SUPPORTED_LANGUAGES[language.language] = display_name

        print(SUPPORTED_LANGUAGES)
        return SUPPORTED_LANGUAGES

    @app.context_processor
    def inject_vars():
        return dict(list_languages=_list_languages, locale=get_locale())

    if running_mode() == 'web':
        initialize_web(app)

    return app
    

def create_mongodb_indexes():
    for column in ['user', 'user_role', 'group', 'laboratory', 'assigned_resource', 
                    'start_reservation', 'max_duration', 'queue_duration', 'reservation_id',
                    'resources', 'features', 'priority', 'start', 'min_end', 'max_end', 
                    'end_reservation']:
        mongo.db.sessions.create_index(column)

SUPPORTED_TRANSLATIONS = None
SUPPORTED_LANGUAGES = None

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
