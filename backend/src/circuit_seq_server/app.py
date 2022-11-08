from __future__ import annotations
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
from circuit_seq_server.logger import get_logger
from circuit_seq_server.date_utils import get_start_of_week
from circuit_seq_server.model import (
    db,
    Sample,
    User,
    add_new_user,
    add_new_sample,
    count_samples_this_week,
    get_current_settings,
    set_current_settings,
    _add_temporary_users_for_testing,
    _add_temporary_samples_for_testing,
)


def create_app(data_path: str = "/circuit_seq_data"):
    app = Flask("CircuitSeqServer")
    app.config["JWT_SECRET_KEY"] = secrets.token_urlsafe(64)
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{data_path}/CircuitSeq.db"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["MAX_CONTENT_LENGTH"] = 64 * 1000 * 1000  # 64mb max file upload

    CORS(app)  # todo: limit ports / routes

    jwt = JWTManager(app)
    db.init_app(app)
    logger = get_logger("CircuitSeqServer")

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

    @app.route("/login", methods=["POST"])
    def login():
        email = request.json.get("email", None)
        password = request.json.get("password", None)
        logger.info(f"Login request from {email}")
        user = db.session.execute(
            db.select(User).filter(User.email == email)
        ).scalar_one_or_none()
        if not user:
            logger.info(f"  -> user not found")
            return jsonify("Unknown email address"), 401
        if not user.activated:
            logger.info(f"  -> user not activated")
            return jsonify("User account is not yet activated"), 401
        if not user.check_password(password):
            logger.info(f"  -> wrong password")
            return jsonify("Incorrect password"), 401
        logger.info(f"  -> returning JWT access token")
        access_token = create_access_token(identity=user)
        return jsonify(user=user.as_dict(), access_token=access_token)

    @app.route("/signup", methods=["POST"])
    def signup():
        email = request.json.get("email", None)
        password = request.json.get("password", None)
        logger.info(f"Signup request from {email}")
        if add_new_user(email, password):
            logger.info(f"  -> signup successful")
            logger.info(f"  -> [todo] activation email sent")
            return jsonify(result="success")
        return jsonify(result="Signup failed"), 401

    @app.route("/remaining", methods=["GET"])
    def remaining():
        settings = get_current_settings()
        return jsonify(
            remaining=settings["plate_n_rows"] * settings["plate_n_cols"]
            - count_samples_this_week()
        )

    @app.route("/samples", methods=["GET"])
    @jwt_required()
    def samples():
        start_of_week = get_start_of_week()
        current_samples = (
            db.session.execute(
                db.select(Sample)
                .filter(Sample.email == current_user.email)
                .filter(Sample.date >= start_of_week)
                .order_by(db.desc("date"))
            )
            .scalars()
            .all()
        )
        previous_samples = (
            db.session.execute(
                db.select(Sample)
                .filter(Sample.email == current_user.email)
                .filter(Sample.date < start_of_week)
                .order_by(db.desc("date"))
            )
            .scalars()
            .all()
        )
        return jsonify(
            current_samples=current_samples, previous_samples=previous_samples
        )

    @app.route("/reference_sequence", methods=["POST"])
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
            return jsonify("Sample not found"), 401
        if user_sample.reference_sequence_description is None:
            logger.info(
                f"  -> sample with key {primary_key} found but does not contain a reference sequence"
            )
            return jsonify("Sample does not contain a reference sequence"), 401
        logger.info(
            f"  -> found reference sequence with description {user_sample.reference_sequence_description}"
        )
        year, week, day = user_sample.date.isocalendar()
        filename = f"{data_path}/{year}/{week}/reference/{user_sample.primary_key}_{user_sample.name}.fasta"
        file = pathlib.Path(filename)
        if not file.is_file():
            logger.info(f"  -> fasta file {file} not found")
            return jsonify("Fasta file not found"), 401
        logger.info(f"Returning fasta file {file}")
        return flask.send_file(file, as_attachment=True)

    @app.route("/addsample", methods=["POST"])
    @jwt_required()
    def add_sample():
        email = current_user.email
        name = request.form.to_dict().get("name", "")
        reference_sequence_file = request.files.to_dict().get("file", None)
        logger.info(f"Adding sample {name} from {email}")
        new_sample = add_new_sample(email, name, reference_sequence_file, data_path)
        if new_sample is not None:
            logger.info(f"  - > success")
            return jsonify(sample=new_sample)
        return jsonify(message="No more samples available this week."), 401

    @app.route("/admin/settings", methods=["GET", "POST"])
    @jwt_required()
    def admin_settings():
        if not current_user.is_admin:
            return jsonify("Admin account required"), 401
        if flask.request.method == "POST":
            if set_current_settings(current_user.email, request.json):
                return jsonify(message="Settings updated.")
            else:
                jsonify(message="Failed to update settings"), 401
        else:
            return get_current_settings()

    @app.route("/admin/allsamples", methods=["GET"])
    @jwt_required()
    def admin_all_samples():
        if not current_user.is_admin:
            return jsonify("Admin account required"), 401
        year, week, day = datetime.date.today().isocalendar()
        start_of_week = datetime.date.fromisocalendar(year, week, 1)
        current_samples = (
            db.session.execute(
                db.select(Sample)
                .filter(Sample.date >= start_of_week)
                .order_by(db.desc("date"))
            )
            .scalars()
            .all()
        )
        previous_samples = (
            db.session.execute(
                db.select(Sample)
                .filter(Sample.date < start_of_week)
                .order_by(db.desc("date"))
            )
            .scalars()
            .all()
        )
        return jsonify(
            current_samples=current_samples, previous_samples=previous_samples
        )

    @app.route("/admin/allusers", methods=["GET"])
    @jwt_required()
    def admin_all_users():
        if current_user.is_admin:
            users = db.session.execute(db.select(User)).scalars().all()
            return jsonify(users=[user.as_dict() for user in users])
        return jsonify("Admin account required"), 401

    with app.app_context():
        db.create_all()
        _add_temporary_users_for_testing()
        _add_temporary_samples_for_testing()

    return app
