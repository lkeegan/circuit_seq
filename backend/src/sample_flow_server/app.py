from __future__ import annotations

import os
import secrets
import pathlib
import datetime
import flask
from flask import Flask
from flask import jsonify
from flask import request
from flask_jwt_extended import create_access_token
from flask_jwt_extended import current_user
from flask_jwt_extended import jwt_required
from flask_jwt_extended import JWTManager
from flask_cors import CORS
from sample_flow_server.logger import get_logger
from sample_flow_server.model import (
    db,
    Sample,
    User,
    add_new_user,
    reset_user_password,
    activate_user,
    add_new_sample,
    get_samples,
    remaining_samples_this_week,
    get_current_settings,
    set_current_settings,
    update_samples_zipfile,
    process_result,
    send_password_reset_email,
)


def create_app(data_path: str = "/sample_flow_data"):
    logger = get_logger("SampleFlowServer")
    app = Flask("SampleFlowServer")
    jwt_secret_key = os.environ.get("JWT_SECRET_KEY")
    if jwt_secret_key is not None and len(jwt_secret_key) > 16:
        logger.info("Setting JWT_SECRET_KEY from supplied env var")
        app.config["JWT_SECRET_KEY"] = jwt_secret_key
    else:
        logger.warning(
            "JWT_SECRET_KEY env var not set or too short: generating random secret key"
        )
        # new secret key -> invalidates any existing tokens
        app.config["JWT_SECRET_KEY"] = secrets.token_urlsafe(64)
    # tokens are by default valid for 30mins
    app.config["JWT_ACCESS_TOKEN_EXPIRES"] = datetime.timedelta(minutes=30)
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{data_path}/SampleFlow.db"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    # limit max file upload size to 384mb
    app.config["MAX_CONTENT_LENGTH"] = 384 * 1024 * 1024
    app.config["CIRCUITSEQ_DATA_PATH"] = data_path

    CORS(app)

    jwt = JWTManager(app)
    db.init_app(app)

    # https://flask-jwt-extended.readthedocs.io/en/stable/api/#flask_jwt_extended.JWTManager.user_identity_loader
    @jwt.user_identity_loader
    def user_identity_lookup(user):
        return user.id

    # https://flask-jwt-extended.readthedocs.io/en/stable/api/#flask_jwt_extended.JWTManager.user_lookup_loader
    @jwt.user_lookup_loader
    def user_lookup_callback(_jwt_header, jwt_data):
        identity = jwt_data["sub"]
        return db.session.execute(
            db.select(User).filter(User.id == identity)
        ).scalar_one_or_none()

    @app.route("/api/login", methods=["POST"])
    def login():
        email = request.json.get("email", None)
        password = request.json.get("password", None)
        logger.info(f"Login request from {email}")
        user = db.session.execute(
            db.select(User).filter(User.email == email)
        ).scalar_one_or_none()
        if not user:
            logger.info("  -> user not found")
            return jsonify(message="Unknown email address"), 401
        if not user.activated:
            logger.info("  -> user not activated")
            return jsonify(message="User account is not yet activated"), 401
        if not user.check_password(password):
            logger.info("  -> wrong password")
            return jsonify(message="Incorrect password"), 401
        logger.info("  -> returning JWT access token")
        access_token = create_access_token(identity=user)
        return jsonify(user=user.as_dict(), access_token=access_token)

    @app.route("/api/signup", methods=["POST"])
    def signup():
        email = request.json.get("email", None)
        password = request.json.get("password", None)
        logger.info(f"Signup request from {email}")
        message, code = add_new_user(email, password, False)
        return jsonify(message=message), code

    @app.route("/api/activate/<token>")
    def activate(token: str):
        message, code = activate_user(token)
        return jsonify(message=message), code

    @app.route("/api/request_password_reset", methods=["POST"])
    def request_password_reset():
        message, code = send_password_reset_email(request.json.get("email", ""))
        return jsonify(message=message), code

    @app.route("/api/reset_password", methods=["POST"])
    def reset_password():
        token = request.json.get("reset_token", None)
        if token is None:
            return jsonify(message="Reset token missing"), 401
        email = request.json.get("email", None)
        if email is None:
            return jsonify(message="Email address missing"), 401
        new_password = request.json.get("new_password", None)
        if new_password is None:
            return jsonify(message="New password missing"), 401
        message, code = reset_user_password(token, email, new_password)
        return jsonify(message=message), code

    @app.route("/api/change_password", methods=["POST"])
    @jwt_required()
    def change_password():
        current_password = request.json.get("current_password", None)
        if current_password is None:
            return jsonify(message="Current password missing"), 401
        new_password = request.json.get("new_password", None)
        if new_password is None:
            return jsonify(message="New password missing"), 401
        logger.info(f"Password change request from {current_user.email}")
        if current_user.set_password(current_password, new_password):
            return jsonify(message="Password changed.")
        return (
            jsonify(message="Failed to change password: current password incorrect."),
            401,
        )

    @app.route("/api/remaining", methods=["GET"])
    def remaining():
        return remaining_samples_this_week()

    @app.route("/api/running_options", methods=["GET"])
    @jwt_required()
    def running_options():
        settings = get_current_settings()
        return jsonify(running_options=settings["running_options"])

    @app.route("/api/samples", methods=["GET"])
    @jwt_required()
    def samples():
        return get_samples(current_user.email)

    @app.route("/api/reference_sequence", methods=["POST"])
    @jwt_required()
    def reference_sequence():
        primary_key = request.json.get("primary_key", None)
        logger.info(
            f"User {current_user.email} requesting reference sequence with key {primary_key}"
        )
        filters = {"primary_key": primary_key}
        if not current_user.is_admin:
            filters["email"] = current_user.email
        user_sample = db.session.execute(
            db.select(Sample).filter_by(**filters)
        ).scalar_one_or_none()
        if user_sample is None:
            logger.info(f"  -> sample with key {primary_key} not found")
            return jsonify(message="Sample not found"), 401
        if user_sample.reference_sequence_description is None:
            logger.info(
                f"  -> sample with key {primary_key} found but does not contain a reference sequence"
            )
            return jsonify(message="Sample does not contain a reference sequence"), 401
        logger.info(
            f"  -> found reference sequence with description {user_sample.reference_sequence_description}"
        )
        year, week, day = user_sample.date.isocalendar()
        filename = f"{data_path}/{year}/{week}/inputs/references/{user_sample.primary_key}_{user_sample.name}.fasta"
        fasta_file = pathlib.Path(filename)
        if not fasta_file.is_file():
            logger.info(f"  -> fasta file {fasta_file} not found")
            return jsonify(message="Fasta file not found"), 401
        logger.info(f"Returning fasta file {fasta_file}")
        return flask.send_file(fasta_file, as_attachment=True)

    @app.route("/api/result", methods=["POST"])
    @jwt_required()
    def result():
        primary_key = request.json.get("primary_key", None)
        filetype = request.json.get("filetype", None)
        if filetype not in ["fasta", "gbk", "zip"]:
            logger.info(f"  -> invalid filetype {filetype} requested")
            return jsonify(message=f"Invalid filetype {filetype} requested"), 401
        logger.info(
            f"User {current_user.email} requesting {filetype} results for key {primary_key}"
        )
        filters = {"primary_key": primary_key}
        if not current_user.is_admin:
            filters["email"] = current_user.email
        user_sample = db.session.execute(
            db.select(Sample).filter_by(**filters)
        ).scalar_one_or_none()
        if user_sample is None:
            logger.info(f"  -> sample with key {primary_key} not found")
            return jsonify(message="Sample not found"), 401
        if (
            (filetype == "fasta" and not user_sample.has_results_fasta)
            or (filetype == "gbk" and not user_sample.has_results_gbk)
            or (filetype == "zip" and not user_sample.has_results_zip)
        ):
            logger.info(
                f"  -> sample with key {primary_key} found but no {filetype} results available"
            )
            return jsonify(message=f"No {filetype} results available"), 401
        year, week, day = user_sample.date.isocalendar()
        filename = f"{data_path}/{year}/{week}/results/{user_sample.primary_key}_{user_sample.name}.{filetype}"
        requested_file = pathlib.Path(filename)
        if not requested_file.is_file():
            logger.info(f"  -> {filetype} file {requested_file} not found")
            return jsonify(message=f"Results {filetype} file not found"), 401
        logger.info(f"Returning {filetype} file {requested_file}")
        return flask.send_file(requested_file, as_attachment=True)

    @app.route("/api/sample", methods=["POST"])
    @jwt_required()
    def add_sample():
        email = current_user.email
        form_as_dict = request.form.to_dict()
        name = form_as_dict.get("name", "")
        running_option = form_as_dict.get("running_option", "")
        concentration = int(form_as_dict.get("concentration", "0"))
        reference_sequence_file = request.files.to_dict().get("file", None)
        logger.info(f"Adding sample {name} from {email}")
        new_sample, error_message = add_new_sample(
            email, name, running_option, concentration, reference_sequence_file
        )
        if new_sample is not None:
            logger.info(f"  - > success")
            return jsonify(sample=new_sample)
        return jsonify(message=error_message), 401

    @app.route("/api/admin/settings", methods=["GET", "POST"])
    @jwt_required()
    def admin_settings():
        if not current_user.is_admin:
            return jsonify(message="Admin account required"), 401
        if flask.request.method == "POST":
            message, code = set_current_settings(current_user.email, request.json)
            return jsonify(message=message), code
        else:
            return get_current_settings()

    @app.route("/api/admin/samples", methods=["GET"])
    @jwt_required()
    def admin_all_samples():
        if not current_user.is_admin:
            return jsonify(message="Admin account required"), 401
        return jsonify(get_samples())

    @app.route("/api/admin/zipsamples", methods=["POST"])
    @jwt_required()
    def admin_zip_samples():
        if not current_user.is_admin:
            return jsonify(message="Admin account required"), 401
        logger.info(
            f"Request for zipfile of samples from Admin user {current_user.email}"
        )
        zip_file = update_samples_zipfile(datetime.date.today())
        return flask.send_file(zip_file, as_attachment=True)

    @app.route("/api/admin/users", methods=["GET"])
    @jwt_required()
    def admin_users():
        if current_user.is_admin:
            users = db.session.execute(db.select(User)).scalars().all()
            return jsonify(users=[user.as_dict() for user in users])
        return jsonify(message="Admin account required"), 401

    @app.route("/api/admin/token", methods=["GET"])
    @jwt_required()
    def admin_token():
        if current_user.is_admin:
            access_token = create_access_token(
                identity=current_user, expires_delta=datetime.timedelta(weeks=26)
            )
            return jsonify(access_token=access_token)
        return jsonify(message="Admin account required"), 401

    @app.route("/api/admin/result", methods=["POST"])
    @jwt_required()
    def admin_upload_result():
        if not current_user.is_admin:
            return jsonify(message="Admin account required"), 401
        email = current_user.email
        form_as_dict = request.form.to_dict()
        primary_key = form_as_dict.get("primary_key", "")
        success = request.form.to_dict().get("success", None)
        logger.info(f"Result upload for '{primary_key}' from user {email}")
        if success is None or success.lower() not in ["true", "false"]:
            logger.info(f"  -> missing success key")
            return jsonify(message="Missing key: success=True/False"), 401
        success = success.lower() == "true"
        zipfile = request.files.to_dict().get("file", None)
        if success is True and zipfile is None:
            logger.info(f"  -> missing zipfile")
            return jsonify(message="Result has success=True but no file"), 401
        message, code = process_result(primary_key, success, zipfile)
        return jsonify(message=message), code

    with app.app_context():
        db.create_all()

    return app
