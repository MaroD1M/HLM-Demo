from flask_sqlalchemy import SQLAlchemy
from flask_bcrypt import Bcrypt
from apscheduler.schedulers.background import BackgroundScheduler


db = SQLAlchemy()
bcrypt = Bcrypt()
scheduler = BackgroundScheduler(timezone='UTC')
