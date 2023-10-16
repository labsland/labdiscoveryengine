from flask import Blueprint, redirect, session, url_for, request
from flask_babel import gettext
from flask_wtf import FlaskForm

from wtforms import StringField
from wtforms import PasswordField
from wtforms import SubmitField
from wtforms import BooleanField
from wtforms.validators import DataRequired

from labdiscoveryengine.views.utils import render_themed_template
from labdiscoveryengine.utils import lde_config

login_blueprint = Blueprint('login', __name__)


class LoginForm(FlaskForm):
    username = StringField('Username', validators=[DataRequired()])
    password = PasswordField('Password', validators=[DataRequired()])
    remember_me = BooleanField('Remember Me')


@login_blueprint.route('/', methods=['GET', 'POST'])
def index():
    return render_themed_template('index.html')


@login_blueprint.route('/login', methods=['GET', 'POST'])
def login():
    form = LoginForm()
    if request.method == 'POST':
        if form.validate_on_submit():
            username = form.username.data 
            password = form.password.data

            if username in lde_config.administrators:
                if lde_config.administrators[username].check_password_hash(password):
                    session['username'] = form.username.data
                    session['role'] = 'admin'
                    return redirect(url_for('user.index'))
                
            if username in lde_config.external_users:
                if lde_config.external_users[username].check_password_hash(password):
                    session['username'] = form.username.data
                    session['role'] = 'external'
                    return redirect(url_for('user.index'))
                
            form.username.errors.append(gettext("Invalid username or password"))
    
    return render_themed_template('login.html', form=form)
