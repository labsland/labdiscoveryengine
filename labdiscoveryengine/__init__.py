from flask import Flask
from werkzeug.security import generate_password_hash

def create_app(config_name: str = 'default'):

    app = Flask(__name__)

    from .configuration.variables import configurations
    app.config.from_object(configurations[config_name])

    
    return app