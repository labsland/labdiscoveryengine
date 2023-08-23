from flask import Flask
from werkzeug.security import generate_password_hash

def create_app(config_name: str = 'default'):

    app = Flask(__name__)

    
    return app