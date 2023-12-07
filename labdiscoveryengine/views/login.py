from flask import Blueprint, redirect, session, url_for, request, flash, g
from flask_babel import gettext
from flask_wtf import FlaskForm

from wtforms import StringField
from wtforms import PasswordField
from wtforms import SubmitField
from wtforms import BooleanField
from wtforms.validators import DataRequired

from labdiscoveryengine import db
from labdiscoveryengine.models import User
from labdiscoveryengine.views.utils import render_themed_template
from labdiscoveryengine.utils import lde_config, is_sql_active

login_blueprint = Blueprint('login', __name__)


class LoginForm(FlaskForm):
    username = StringField('Username', validators=[DataRequired()])
    password = PasswordField('Password', validators=[DataRequired()])
    remember_me = BooleanField('Remember Me')


class LogoutForm(FlaskForm):
    pass


@login_blueprint.before_request
def before_request():
    logout_form = LogoutForm()
    g.logout_form = logout_form


@login_blueprint.context_processor
def inject_vars():
    username = session.get('username')
    role = session.get('role')
    return dict(username=username, role=role)


@login_blueprint.route('/', methods=['GET', 'POST'])
def index():
    return render_themed_template('index.html')


@login_blueprint.route('/login', methods=['GET', 'POST'])
def login():

    # If we are already logged in we redirect to the labs screen.
    username = session.get('username')
    role = session.get('role')
    url = request.args.get('url')
    if not url or not url.startswith('/'):
        url = url_for('user.index')

    if username is not None and role is not None:
        return redirect(url)

    form = LoginForm()
    if request.method == 'POST':
        if form.validate_on_submit():
            username = form.username.data 
            password = form.password.data

            if username in lde_config.administrators:
                if lde_config.administrators[username].check_password_hash(password):
                    session['username'] = form.username.data
                    session['role'] = 'admin'
                    session['is_db'] = False
                    return redirect(url)
                
            if username in lde_config.external_users:
                if lde_config.external_users[username].check_password_hash(password):
                    session['username'] = form.username.data
                    session['role'] = 'external'
                    session['is_db'] = False
                    return redirect(url)
            
            if is_sql_active():
                user = db.session.query(User).filter(User.login == username).first()
                if user is not None:
                    if user.check_password(password):
                        session['username'] = form.username.data
                        # It would be possible to allow additional admins in the database
                        # To be decided
                        session['role'] = 'student'
                        session['is_db'] = True
                        return redirect(url)
                
            form.username.errors.append(gettext("Invalid username or password"))
    
    return render_themed_template('login.html', form=form)


@login_blueprint.route('/logout', methods=['POST'])
def logout():
    form = LogoutForm()
    if form.validate_on_submit():
        session.pop('username', None)
        session.pop('role', None)
        # flash('You have been logged out.')
        return redirect(url_for('login.login'))
    else:
        flash('Logout failed. CSRF check failed.')
        return redirect(url_for('user.index'))
