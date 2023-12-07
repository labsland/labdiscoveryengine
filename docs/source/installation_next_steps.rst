Installation: next steps
========================

Adding users
------------

If you are using LabDiscoveryEngine for using it with students, you will need to install a database (such as MySQL, MariaDB, PosgreSQL, SQLite, or similar).

In the case of MySQL, once you have installed it, you can create a database (please use a proper password) by doing the following steps as root::

    $ sudo mysql -uroot

    mysql> create database lde default charset utf8;
    Query OK, 1 row affected, 1 warning (0.00 sec)

    mysql> create user lde@localhost identified by 'ldepassword';
    Query OK, 0 rows affected (0.02 sec)

    mysql> grant all privileges on lde.* to lde@localhost;
    Query OK, 0 rows affected (0.00 sec)

    mysql> flush privileges;
    Query OK, 0 rows affected (0.02 sec)

Once you do this, you will need to configure LabDiscoveryEngine to actually use this database. This requires two steps:

 1. First, you need to configure in ``configuration.yml`` the variable ``SQLALCHEMY_DATABASE_URI``. Please refer to the `SQLAlchemy documentation <https://docs.sqlalchemy.org/en/latest/core/engines.html#backend-specific-urls>`_ to see the specifics for any database. In the case of MySQL, you need to add the following line (change password, name of the database or user)::
    
        SQLALCHEMY_DATABASE_URI: mysql+pymysql://lde:ldepassword@localhost:3306/lde # Optional
    
 1. Then, you have to run the following command to create the basic structure of tables and indexes::

        lde deployments db upgrade

After doing this, if you start the that setup, you should see a way to create users in the Administration Panel.

Storing sessions
----------------

If you would like to store what students do, you need to install MongoDB.

Once you install it, you have to set a ``MONGO_URI`` in the ``configuration.yml`` file of your deployment. Please refer to the `MongoDB documentation <https://www.mongodb.com/docs/manual/reference/connection-string/>`_ on the parameters of a MongoDB connection URL, but here you have a basic one::

    MONGO_URI: "mongodb://localhost:27017/lde"

After doing this, you should restart your LDE and you should start seeing in the Administration Panel that accesses are being stored.
