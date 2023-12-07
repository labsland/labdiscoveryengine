import hashlib
from wtforms import fields, widgets, validators, form
from flask import redirect, request, session, url_for
from flask_admin import Admin, AdminIndexView
from flask_admin.form.widgets import Select2Widget
from flask_babel import gettext
from flask_admin import Admin, expose, BaseView
from flask_admin.contrib.sqla import ModelView
from flask_admin.model.form import InlineFormAdmin
import flask_admin.contrib.pymongo as flask_admin_pymongo
from flask_admin.model import filters

from labdiscoveryengine import mongo, db
from labdiscoveryengine.models import GroupPermission, User, Group
from labdiscoveryengine.utils import lde_config, slugify, is_mongo_active, is_sql_active

class AuthMixIn:
    def is_accessible(self):
        return session.get('role') == 'admin'

    def inaccessible_callback(self, name, **kwargs):
        if session.get('role') is None:
            return redirect(url_for('login.login', url=request.full_path))
        
        return "Only admins can use the administration panel"

    def render(self, template, **kwargs):
        """
        using extra js in render method allow use
        url_for that itself requires an app context
        """
        self.extra_js = []

        return super().render(template, **kwargs)
    
class MainIndexView(AuthMixIn, AdminIndexView):
    @expose('/')
    def index(self):
        mongo_active = is_mongo_active()
        sql_active = is_sql_active()
        return self.render('lde-admin/index.html', mongo_active=mongo_active, sql_active=sql_active)

class NoPyMongoView(AuthMixIn, BaseView):
    @expose('/')
    def index(self):
        return self.render("lde-admin/no-pymongo.html")

class NoSQLAlchemyView(AuthMixIn, BaseView):
    @expose('/')
    def index(self):
        return self.render("lde-admin/no-sqlalchemy.html")

class UsersView(AuthMixIn, ModelView):

    column_list = ['login', 'full_name', 'groups', 'last_login', 'created_at', 'updated_at']

    column_searchable_list = ['login', 'full_name']
    column_filters = ['login', 'full_name', 'last_login', 'created_at', 'updated_at', 'groups.name']

    form_columns = ['login', 'full_name', 'password', 'confirm_password', 'groups']
    form_extra_fields = {
        'password': fields.PasswordField('Password'),
        'confirm_password': fields.PasswordField('Confirm password')
    }

    form_widget_args = {
        'login': {
            'autofocus': 'autofocus'
        }
    }

    def create_form(self):
        form = super().create_form()
        form.password.validators = [validators.DataRequired()]
        form.confirm_password.validators = [validators.DataRequired()]
        return form

    def on_model_change(self, form, model: User, is_created):
        if is_created:
            model.on_create()
            if not form.password.data:
                raise validators.ValidationError(gettext('Password is required'))
        else:
            model.on_update()

        model.role = 'student'

        if form.login.data in lde_config.administrators:
            raise validators.ValidationError(gettext(f'User {form.login.data} already exists as administrator in credentials.yml'))
        
        if form.login.data in lde_config.external_users:
            raise validators.ValidationError(gettext(f'User {form.login.data} already exists as external user in credentials.yml'))

        if form.password.data or form.confirm_password.data:
            if form.password.data == form.confirm_password.data:
                model.change_password(form.password.data)
            else:
                raise validators.ValidationError(gettext('Passwords do not match'))
        return super().on_model_change(form, model, is_created)

def create_laboratory_hash(laboratory: str, hash_length=6) -> str:
    slug = slugify(laboratory)
    hash_object = hashlib.md5(laboratory.encode())
    hash_digest = hash_object.hexdigest()[:hash_length]  # Taking first 6 characters of the MD5 hash
    return f"{slug}-{hash_digest}"


def create_laboratories_by_hashed_id(hash_length=6):
    slug_hash_dict = {}
    for laboratory in lde_config.laboratories:
        key = create_laboratory_hash(laboratory, hash_length)
        slug_hash_dict[key] = laboratory
    return slug_hash_dict

def get_laboratories_list():
    full_list = list(create_laboratories_by_hashed_id().items())
    full_list.sort(key=lambda x: x[1]) # Sort by the name
    return full_list

class Select2MultipleField(fields.SelectMultipleField):
    widget = Select2Widget(multiple=True)

class GroupsView(AuthMixIn, ModelView):

    column_list = ['name', 'created_at', 'updated_at', 'permissions']

    column_searchable_list = ['name']
    column_filters = ['name', 'created_at', 'updated_at', 'users.login', 'users.full_name', 'permissions.laboratory']

    form_columns = ['name', 'users', 'permissions2']

    form_extra_fields = {
        'permissions2': Select2MultipleField('Permissions')
    }

    form_widget_args = {
        'name': {
            'autofocus': 'autofocus'
        }
    }

    def on_form_prefill(self, form, id):
        form.permissions2.choices = get_laboratories_list()
        super().on_form_prefill(form, id)

    def create_form(self):
        form = super().create_form()
        form.permissions2.choices = get_laboratories_list()
        return form
    
    def edit_form(self, obj):
        form = super().edit_form(obj)
        form.permissions2.choices = get_laboratories_list()
        if request.method == 'GET':
            form.permissions2.data = [
                create_laboratory_hash(p.laboratory) 
                for p in obj.permissions
                if p.laboratory in lde_config.laboratories
            ]
        return form

    def on_model_change(self, form, model: User, is_created: bool):
        if is_created:
            model.on_create()
        else:
            model.on_update()

        existing_hashes = set([])
        for permission in model.permissions:
            hashed_lab_name = create_laboratory_hash(permission.laboratory)
            if hashed_lab_name not in form.permissions2.data or permission.laboratory not in lde_config.laboratories:
                self.session.delete(permission)
            else:
                existing_hashes.add(hashed_lab_name)
        
        labs_by_hashed_name = create_laboratories_by_hashed_id()
        for new_hashed_lab_name in set(form.permissions2.data).difference(existing_hashes):
            original_name = labs_by_hashed_name.get(new_hashed_lab_name)
            if original_name and original_name in lde_config.laboratories:
                group_permission = GroupPermission(group=model, laboratory=original_name)
                self.session.add(group_permission)

        return super().on_model_change(form, model, is_created)

class UserSessionForm(form.Form):  
    pass

class UserSessionsView(AuthMixIn, flask_admin_pymongo.ModelView):
    column_list = ['user', 'user_role', 'group', 'laboratory', 'assigned_resource', 'start_reservation', 'max_duration', 'queue_duration']

    can_create = False
    can_edit = False
    can_delete = False

    form = UserSessionForm


def create_admin(app) -> Admin:
    admin = Admin(name='LabDiscoveryEngine', index_view=MainIndexView(), template_mode='bootstrap3')

    if is_sql_active(app):
        admin.add_view(UsersView(User, db.session, name='Users', endpoint="users", url='/admin/users'))
        admin.add_view(GroupsView(Group, db.session, name='Groups', endpoint="groups", url='/admin/groups'))
    else:
        admin.add_view(NoSQLAlchemyView(name='Users', url='/admin/users', endpoint="users"))
        admin.add_view(NoSQLAlchemyView(name='Groups', url='/admin/groups', endpoint="groups"))

    if is_mongo_active(app):
        admin.add_view(UserSessionsView(mongo.db.sessions, name='User sessions', url="/admin/users/sessions", endpoint="user_sessions"))
    else:
        admin.add_view(NoPyMongoView(name='User sessions', url="/admin/users/sessions", endpoint="user_sessions"))

    return admin