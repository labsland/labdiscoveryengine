import datetime
from werkzeug.security import generate_password_hash, check_password_hash

from labdiscoveryengine import db

user_in_group  = db.Table('user_in_group', db.Model.metadata,
                        db.Column('user_id', db.Integer, db.ForeignKey('users.id')),
                        db.Column('group_id', db.Integer, db.ForeignKey('groups.id'))
                    )

class User(db.Model):
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True) # pylint: disable=invalid-name
    login = db.Column(db.String(64), index=True, unique=True, nullable=False)
    full_name = db.Column(db.String(255), index=True, nullable=True) # do not require
    role = db.Column(db.String(255), index=True, nullable=False) # probably always 'student'
    salted_password = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, index=True, nullable=False)
    updated_at = db.Column(db.DateTime, index=True, nullable=False)
    last_login = db.Column(db.DateTime, index=True, nullable=True)

    groups = db.relation("Group", secondary=user_in_group, back_populates='users')

    def on_create(self):
        self.created_at = datetime.datetime.now(datetime.timezone.utc)
        self.updated_at = self.created_at
        self.last_login = None

    def on_update(self):
        self.updated_at = datetime.datetime.now(datetime.timezone.utc)

    def on_access(self):
        self.last_login = datetime.datetime.now(datetime.timezone.utc)

    def change_password(self, password):
        self.salted_password = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.salted_password, password)
    
    def __str__(self):
        if self.full_name:
            return f"{self.full_name} ({self.login})"
        return self.login

class Group(db.Model):
    __tablename__ = 'groups'

    id = db.Column(db.Integer, primary_key=True) # pylint: disable=invalid-name
    name = db.Column(db.String(255), index=True, nullable=False)
    created_at = db.Column(db.DateTime, index=True, nullable=False)
    updated_at = db.Column(db.DateTime, index=True, nullable=False)

    users = db.relation("User", secondary=user_in_group, back_populates='groups')

    def on_create(self):
        self.created_at = datetime.datetime.now(datetime.timezone.utc)
        self.updated_at = self.created_at

    def on_update(self):
        self.updated_at = datetime.datetime.now(datetime.timezone.utc)

    def __str__(self):
        return self.name

class GroupPermission(db.Model):
    __tablename__ = 'group_permissions'

    id = db.Column(db.Integer, primary_key=True) # pylint: disable=invalid-name
    group_id = db.Column(db.Integer, db.ForeignKey('groups.id'), nullable=False)
    laboratory = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, index=True, nullable=False)

    group = db.relationship('Group', backref=db.backref('permissions', lazy=True))

    def __init__(self, group, laboratory):
        self.group = group
        self.laboratory = laboratory
        self.created_at = datetime.datetime.now(datetime.timezone.utc)

    def __str__(self):
        return self.laboratory
