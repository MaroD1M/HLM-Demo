from flask_sqlalchemy import SQLAlchemy
from apscheduler.schedulers.background import BackgroundScheduler


db = SQLAlchemy()
scheduler = BackgroundScheduler(timezone='UTC')
