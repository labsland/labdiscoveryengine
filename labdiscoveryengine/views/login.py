from flask import Blueprint, redirect, session, url_for, request
from flask_wtf import Form

from wtforms import StringField
from wtforms import PasswordField
from wtforms import SubmitField
from wtforms import BooleanField
from wtforms.validators import DataRequired

from labdiscoveryengine.views.utils import render_themed_template

login_blueprint = Blueprint('login', __name__)

class LoginForm(Form):
    username = StringField('Username', validators=[DataRequired()])
    password = PasswordField('Password', validators=[DataRequired()])
    remember_me = BooleanField('Remember Me')

@login_blueprint.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        form = LoginForm()
        if form.validate_on_submit():
            # TODO: do the authentication part
            session['username'] = form.username.data
            return redirect(url_for('index'))
    
    return render_themed_template('login.html')