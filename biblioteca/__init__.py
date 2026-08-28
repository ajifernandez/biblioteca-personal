import os
from flask import Flask

from .db import close_db, init_db


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(BASE_DIR)
VERSION_PATH = os.path.join(PROJECT_ROOT, "VERSION")


def load_app_version():
    env_version = os.environ.get("APP_VERSION")
    if env_version:
        return env_version
    try:
        with open(VERSION_PATH, encoding="utf-8") as f:
            return f.read().strip() or "dev"
    except OSError:
        return "dev"


APP_VERSION = load_app_version()


def create_app() -> Flask:
    app = Flask(
        __name__,
        static_folder=os.path.join(PROJECT_ROOT, "static"),
        template_folder=os.path.join(PROJECT_ROOT, "templates"),
    )

    @app.context_processor
    def inject_app_version():
        return {"app_version": APP_VERSION}

    # Blueprints
    from .routes.pages import pages
    from .routes.books import books
    from .routes.api import api
    from .routes.uploads import uploads

    app.register_blueprint(pages)
    app.register_blueprint(books)
    app.register_blueprint(api, url_prefix="/api")
    app.register_blueprint(uploads, url_prefix="/uploads")

    # DB lifecycle
    app.teardown_appcontext(close_db)
    with app.app_context():
        init_db()

    return app
