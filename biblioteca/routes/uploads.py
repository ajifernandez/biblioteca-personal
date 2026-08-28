from flask import Blueprint, send_from_directory

from ..services import UPLOAD_DIR

uploads = Blueprint("uploads", __name__)


@uploads.route("/<path:filename>")
def uploaded_cover(filename):
    return send_from_directory(UPLOAD_DIR, filename)
