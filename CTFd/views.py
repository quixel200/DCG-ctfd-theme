import os  # noqa: I001

from flask import Blueprint, abort
from flask import current_app as app
from flask import (
    make_response,
    send_from_directory,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from jinja2.exceptions import TemplateNotFound
from sqlalchemy.exc import IntegrityError
from werkzeug.utils import safe_join

from CTFd.cache import cache
from CTFd.constants.config import (
    AccountVisibilityTypes,
    ChallengeVisibilityTypes,
    ConfigTypes,
    RegistrationVisibilityTypes,
    ScoreVisibilityTypes,
)
from CTFd.constants.themes import DEFAULT_THEME
from CTFd.models import (
    Admins,
    Files,
    Notifications,
    Pages,
    Solutions,
    Teams,
    Users,
    UserTokens,
    db,
)
from CTFd.utils import config, get_config, set_config
from CTFd.utils import user as current_user
from CTFd.utils import validators
from CTFd.utils.config import can_send_mail, is_setup, is_teams_mode
from CTFd.utils.config.pages import build_markdown, get_page
from CTFd.utils.config.visibility import challenges_visible
from CTFd.utils.dates import ctf_ended, ctftime, view_after_ctf
from CTFd.utils.decorators import authed_only
from CTFd.utils.health import check_config, check_database
from CTFd.utils.helpers import get_errors, get_infos, markup
from CTFd.utils.modes import USERS_MODE
from CTFd.utils.security.auth import login_user
from CTFd.utils.security.csrf import generate_nonce
from CTFd.utils.security.signing import (
    BadSignature,
    BadTimeSignature,
    SignatureExpired,
    serialize,
    unserialize,
)
from CTFd.utils.uploads import get_uploader, upload_file
from CTFd.utils.user import authed, get_current_team, get_current_user, get_ip, is_admin

views = Blueprint("views", __name__)


@views.route("/setup", methods=["GET", "POST"])
def setup():
    errors = get_errors()
    if not config.is_setup():
        if not session.get("nonce"):
            session["nonce"] = generate_nonce()
        if request.method == "POST":
            # General
            ctf_name = request.form.get("ctf_name")
            ctf_description = request.form.get("ctf_description")
            user_mode = request.form.get("user_mode", USERS_MODE)
            set_config("ctf_name", ctf_name)
            set_config("ctf_description", ctf_description)
            set_config("user_mode", user_mode)

            # Settings
            challenge_visibility = ChallengeVisibilityTypes(
                request.form.get(
                    "challenge_visibility", default=ChallengeVisibilityTypes.PRIVATE
                )
            )
            account_visibility = AccountVisibilityTypes(
                request.form.get(
                    "account_visibility", default=AccountVisibilityTypes.PUBLIC
                )
            )
            score_visibility = ScoreVisibilityTypes(
                request.form.get(
                    "score_visibility", default=ScoreVisibilityTypes.PUBLIC
                )
            )
            registration_visibility = RegistrationVisibilityTypes(
                request.form.get(
                    "registration_visibility",
                    default=RegistrationVisibilityTypes.PUBLIC,
                )
            )
            verify_emails = request.form.get("verify_emails")
            social_shares = request.form.get("social_shares")
            team_size = request.form.get("team_size")

            # Style
            ctf_logo = request.files.get("ctf_logo")
            if ctf_logo:
                f = upload_file(file=ctf_logo)
                set_config("ctf_logo", f.location)

            ctf_small_icon = request.files.get("ctf_small_icon")
            if ctf_small_icon:
                f = upload_file(file=ctf_small_icon)
                set_config("ctf_small_icon", f.location)

            theme = request.form.get("ctf_theme", DEFAULT_THEME)
            set_config("ctf_theme", theme)
            theme_color = request.form.get("theme_color")
            theme_header = get_config("theme_header")
            if theme_color and bool(theme_header) is False:
                # Uses {{ and }} to insert curly braces while using the format method
                css = (
                    '<style id="theme-color">\n'
                    ":root {{--theme-color: {theme_color};}}\n"
                    ".navbar{{background-color: var(--theme-color) !important;}}\n"
                    ".jumbotron{{background-color: var(--theme-color) !important;}}\n"
                    "</style>\n"
                ).format(theme_color=theme_color)
                set_config("theme_header", css)

            # DateTime
            start = request.form.get("start")
            end = request.form.get("end")
            set_config("start", start)
            set_config("end", end)
            set_config("freeze", None)

            # Administration
            name = request.form["name"]
            email = request.form["email"]
            password = request.form["password"]

            name_len = len(name) == 0
            names = (
                Users.query.add_columns(Users.name, Users.id)
                .filter_by(name=name)
                .first()
            )
            emails = (
                Users.query.add_columns(Users.email, Users.id)
                .filter_by(email=email)
                .first()
            )
            pass_short = len(password) == 0
            pass_long = len(password) > 128
            valid_email = validators.validate_email(request.form["email"])
            team_name_email_check = validators.validate_email(name)

            if not valid_email:
                errors.append("Please enter a valid email address")
            if names:
                errors.append("That user name is already taken")
            if team_name_email_check is True:
                errors.append("Your user name cannot be an email address")
            if emails:
                errors.append("That email has already been used")
            if pass_short:
                errors.append("Pick a longer password")
            if pass_long:
                errors.append("Pick a shorter password")
            if name_len:
                errors.append("Pick a longer user name")

            if len(errors) > 0:
                return render_template(
                    "setup.html",
                    errors=errors,
                    name=name,
                    email=email,
                    password=password,
                    state=serialize(generate_nonce()),
                )

            admin = Admins(
                name=name, email=email, password=password, type="admin", hidden=True
            )

            # Create an empty index page
            page = Pages(title=ctf_name, route="index", content="", draft=False)

            # Upload banner
            default_ctf_banner_location = url_for("views.themes", path="img/logo.png")
            ctf_banner = request.files.get("ctf_banner")
            if ctf_banner:
                f = upload_file(file=ctf_banner, page_id=page.id)
                default_ctf_banner_location = url_for("views.files", path=f.location)
                set_config("ctf_banner", f.location)

            # Splice in our banner
            index = f"""<div class="row">
    <div class="col-md-6 offset-md-3">
        <img class="w-100 mx-auto d-block" style="max-width: 500px;padding: 50px;padding-top: 14vh;" src="{default_ctf_banner_location}" />
        <h3 class="text-center">
            <p>A cool CTF platform from <a href="https://ctfd.io">ctfd.io</a></p>
            <p>Follow us on social media:</p>
            <a href="https://twitter.com/ctfdio"><i class="fab fa-twitter fa-2x" aria-hidden="true"></i></a>&nbsp;
            <a href="https://facebook.com/ctfdio"><i class="fab fa-facebook fa-2x" aria-hidden="true"></i></a>&nbsp;
            <a href="https://github.com/ctfd"><i class="fab fa-github fa-2x" aria-hidden="true"></i></a>
        </h3>
        <br>
        <h4 class="text-center">
            <a href="admin">Click here</a> to login and setup your CTF
        </h4>
    </div>
</div>"""
            page.content = index

            # Visibility
            set_config(ConfigTypes.CHALLENGE_VISIBILITY, challenge_visibility)
            set_config(ConfigTypes.REGISTRATION_VISIBILITY, registration_visibility)
            set_config(ConfigTypes.SCORE_VISIBILITY, score_visibility)
            set_config(ConfigTypes.ACCOUNT_VISIBILITY, account_visibility)

            # Verify emails
            set_config("verify_emails", verify_emails)

            # Social shares
            set_config("social_shares", social_shares)

            # Team Size
            set_config("team_size", team_size)

            set_config("mail_server", None)
            set_config("mail_port", None)
            set_config("mail_tls", None)
            set_config("mail_ssl", None)
            set_config("mail_username", None)
            set_config("mail_password", None)
            set_config("mail_useauth", None)

            set_config("setup", True)

            try:
                db.session.add(admin)
                db.session.commit()
            except IntegrityError:
                db.session.rollback()

            try:
                db.session.add(page)
                db.session.commit()
            except IntegrityError:
                db.session.rollback()

            login_user(admin)

            db.session.close()
            with app.app_context():
                cache.clear()

            return redirect(url_for("views.static_html"))
        try:
            return render_template("setup.html", state=serialize(generate_nonce()))
        except TemplateNotFound:
            # Set theme to default and try again
            set_config("ctf_theme", DEFAULT_THEME)
            return render_template("setup.html", state=serialize(generate_nonce()))
    return redirect(url_for("views.static_html"))


@views.route("/setup/integrations", methods=["GET", "POST"])
def integrations():
    if is_admin() or is_setup() is False:
        name = request.values.get("name")
        state = request.values.get("state")

        try:
            state = unserialize(state, max_age=3600)
        except (BadSignature, BadTimeSignature):
            state = False
        except Exception:
            state = False

        if state:
            if name == "mlc":
                mlc_client_id = request.values.get("mlc_client_id")
                mlc_client_secret = request.values.get("mlc_client_secret")
                set_config("oauth_client_id", mlc_client_id)
                set_config("oauth_client_secret", mlc_client_secret)
                return render_template("admin/integrations.html")
            else:
                abort(404)
        else:
            abort(403)
    else:
        abort(403)


@views.route("/notifications", methods=["GET"])
def notifications():
    notifications = Notifications.query.order_by(Notifications.id.desc()).all()
    return render_template("notifications.html", notifications=notifications)


@views.route("/settings", methods=["GET"])
@authed_only
def settings():
    infos = get_infos()
    errors = get_errors()

    user = get_current_user()

    if is_teams_mode() and get_current_team() is None:
        team_url = url_for("teams.private")
        infos.append(
            markup(
                f'In order to participate you must either <a href="{team_url}">join or create a team</a>.'
            )
        )

    tokens = UserTokens.query.filter_by(user_id=user.id).all()

    prevent_name_change = get_config("prevent_name_change")

    if can_send_mail() and not user.verified:
        confirm_url = markup(url_for("auth.confirm", flow="init"))
        infos.append(
            markup(
                "Your email address isn't confirmed!<br>"
                f'To confirm your email address please <a href="{confirm_url}">click here</a>.'
            )
        )

    return render_template(
        "settings.html",
        name=user.name,
        email=user.email,
        language=user.language,
        website=user.website,
        affiliation=user.affiliation,
        country=user.country,
        tokens=tokens,
        prevent_name_change=prevent_name_change,
        infos=infos,
        errors=errors,
    )


@views.route("/", defaults={"route": "index"})
@views.route("/<path:route>")
def static_html(route):
    """
    Route in charge of routing users to Pages.
    :param route:
    :return:
    """
    # --- NEW OUTRO INTERCEPT LOGIC ---
    if route == "index" and not request.args.get('no_outro'):
        outro_state = _check_outro_timer()
        if outro_state['enabled'] and outro_state['replace_index']:
            outro_file = get_config('outro_file') or 'outro.html'
            outro_dir = os.path.abspath(os.path.join(app.root_path, '../outro'))
            return send_from_directory(outro_dir, outro_file)
    # --- END NEW OUTRO LOGIC ---

    page = get_page(route)
    if page is None:
        abort(404)
    else:
        if page.auth_required and authed() is False:
            return redirect(url_for("auth.login", next=request.full_path))

        return render_template("page.html", content=page.html, title=page.title)


@views.route("/tos")
def tos():
    tos_url = get_config("tos_url")
    tos_text = get_config("tos_text")
    if tos_url:
        return redirect(tos_url)
    elif tos_text:
        return render_template("page.html", content=build_markdown(tos_text))
    else:
        abort(404)


@views.route("/privacy")
def privacy():
    privacy_url = get_config("privacy_url")
    privacy_text = get_config("privacy_text")
    if privacy_url:
        return redirect(privacy_url)
    elif privacy_text:
        return render_template("page.html", content=build_markdown(privacy_text))
    else:
        abort(404)


@views.route("/files", defaults={"path": ""})
@views.route("/files/<path:path>")
def files(path):
    """
    Route in charge of dealing with making sure that CTF challenges are only accessible during the competition.
    :param path:
    :return:
    """
    f = Files.query.filter_by(location=path).first_or_404()
    if f.type == "challenge":
        if challenges_visible():
            if current_user.is_admin() is False:
                if not ctftime():
                    if ctf_ended() and view_after_ctf():
                        pass
                    else:
                        abort(403)
        else:
            # User cannot view challenges based on challenge visibility
            # e.g. ctf requires registration but user isn't authed or
            # ctf requires admin account but user isn't admin

            # Allow downloads if a valid token is provided
            # For example with wget downloads
            token = request.args.get("token", "")
            try:
                data = unserialize(token, max_age=3600)
            # The token isn't expired or broken
            except (BadTimeSignature, SignatureExpired, BadSignature):
                abort(403)

            # Determine the user and team asking to download
            user_id = data.get("user_id")
            team_id = data.get("team_id")
            file_id = data.get("file_id")
            user = Users.query.filter_by(id=user_id).first()
            team = Teams.query.filter_by(id=team_id).first()

            if not ctftime():
                # It's not CTF time. The only edge case is if the CTF is ended
                # but we have view_after_ctf enabled
                if ctf_ended() and view_after_ctf():
                    pass
                else:
                    if user.type == "admin":
                        # We allow admins to download files by URL before CTF start
                        pass
                    else:
                        # In all other situations we should block challenge files
                        abort(403)

            # Check user is admin if challenge_visibility is admins only
            if (
                get_config(ConfigTypes.CHALLENGE_VISIBILITY) == "admins"
                and user.type != "admin"
            ):
                abort(403)

            # Check that the user exists and isn't banned
            if user:
                if user.banned:
                    abort(403)
            else:
                abort(403)

            # Check that the team isn't banned
            if team:
                if team.banned:
                    abort(403)
            else:
                pass

            # Check that the token properly refers to the file
            if file_id != f.id:
                abort(403)

    elif f.type == "solution":
        s = Solutions.query.filter_by(id=f.solution_id).first_or_404()
        if s.state != "visible" or s.challenge.state != "visible":
            # Admins can see solution files for preview purposes
            if current_user.is_admin() is True:
                pass
            else:
                abort(404)

    uploader = get_uploader()
    try:
        return uploader.download(f.location)
    except IOError:
        abort(404)

def _check_outro_timer():
    """
    Evaluate if the standard CTFd end time has passed.
    If it has, trigger the outro logic.
    """
    from CTFd.utils.dates import ctf_ended
    
    # We only need 3 simple custom configs now
    outro_enabled = str(get_config('outro_enabled') or 'disabled')
    outro_access = str(get_config('outro_access') or 'authenticated')
    outro_replace_index = str(get_config('outro_replace_index') or '0')
    
    # Check CTFd's built-in end timer
    timer_triggered = ctf_ended()

    if outro_enabled == 'enabled' and timer_triggered:
        # Auto-enable replace index when the CTF ends
        if outro_replace_index != '1':
            set_config('outro_replace_index', '1')
            outro_replace_index = '1'

    return {
        'enabled': outro_enabled == 'enabled',
        'access': outro_access,
        'timer_triggered': timer_triggered,
        'replace_index': outro_replace_index == '1',
        'redirect_to_outro': outro_enabled == 'enabled' and (timer_triggered or outro_replace_index == '1'),
    }

@views.route("/themes/<theme>/static/<path:path>")
def themes(theme, path):
    """
    General static file handler
    :param theme:
    :param path:
    :return:
    """
    for cand_path in (
        safe_join(app.root_path, "themes", cand_theme, "static", path)
        # The `theme` value passed in may not be the configured one, e.g. for
        # admin pages, so we check that first
        for cand_theme in (theme, *config.ctf_theme_candidates())
    ):
        # Handle werkzeug behavior of returning None on malicious paths
        if cand_path is None:
            abort(404)
        if os.path.isfile(cand_path):
            return send_file(cand_path, max_age=3600)
    abort(404)


@views.route("/themes/<theme>/static/<path:path>")
def themes_beta(theme, path):
    """
    This is a copy of the above themes route used to avoid
    the current appending of .dev and .min for theme assets.

    In CTFd 4.0 this url_for behavior and this themes_beta
    route will be removed.
    """
    for cand_path in (
        safe_join(app.root_path, "themes", cand_theme, "static", path)
        # The `theme` value passed in may not be the configured one, e.g. for
        # admin pages, so we check that first
        for cand_theme in (theme, *config.ctf_theme_candidates())
    ):
        # Handle werkzeug behavior of returning None on malicious paths
        if cand_path is None:
            abort(404)
        if os.path.isfile(cand_path):
            return send_file(cand_path, max_age=3600)
    abort(404)


@views.route("/healthcheck")
def healthcheck():
    if check_database() is False:
        return "ERR", 500
    if check_config() is False:
        return "ERR", 500
    return "OK", 200


@views.route("/debug")
def debug():
    if app.config.get("SAFE_MODE") is True:
        ip = get_ip()
        headers = dict(request.headers)
        # Remove Cookie item
        headers.pop("Cookie", None)
        resp = ""
        resp += f"IP: {ip}\n"
        for k, v in headers.items():
            resp += f"{k}: {v}\n"
        r = make_response(resp)
        r.mimetype = "text/plain"
        return r
    abort(404)


@views.route("/robots.txt")
def robots():
    text = get_config("robots_txt", "User-agent: *\nDisallow: /admin\n")
    r = make_response(text, 200)
    r.mimetype = "text/plain"
    return r

def _check_outro_timer():
    """
    Evaluate if the standard CTFd end time has passed.
    If it has, trigger the outro logic.
    """
    from CTFd.utils.dates import ctf_ended
    
    outro_enabled = str(get_config('outro_enabled') or 'disabled')
    outro_access = str(get_config('outro_access') or 'authenticated')
    outro_replace_index = str(get_config('outro_replace_index') or '0')
    
    # Check CTFd's built-in end timer
    timer_triggered = ctf_ended()

    if outro_enabled == 'enabled' and timer_triggered:
        # Auto-enable replace index when the CTF ends
        if outro_replace_index != '1':
            set_config('outro_replace_index', '1')
            outro_replace_index = '1'

    return {
        'enabled': outro_enabled == 'enabled',
        'access': outro_access,
        'timer_triggered': timer_triggered,
        'replace_index': outro_replace_index == '1',
        'redirect_to_outro': outro_enabled == 'enabled' and (timer_triggered or outro_replace_index == '1'),
    }

@views.before_app_request
def global_outro_redirect():
    """
    Runs before every single request. If the outro is active, force 
    everyone on the frontend to the homepage (which serves the outro).
    """
    # 1. Protect critical paths so we don't break the admin panel, APIs, or assets
    exempt_prefixes = (
        "/themes",       # CTFd base styling
        "/login",
        "/api",          # Backend data (including your outro_data)
        "/admin",        # Admin panel
        "/setup",        # Setup pages
        "/outro_assets", # Your custom outro files
        "/files",        # File downloads
        "/auth"          # Login/Logout routes so admins don't get locked out
    )
    
    if request.path.startswith(exempt_prefixes):
        return

    # 2. Check the current outro status
    outro_state = _check_outro_timer()
    
    # 3. If triggered, and they aren't ALREADY on the homepage, redirect them!
    if outro_state['redirect_to_outro'] and request.path != "/":
        return redirect(url_for("views.static_html", route="index"))

@views.route("/outro_assets/<path:path>")
def outro_assets(path):
    return send_from_directory(os.path.abspath(os.path.join(app.root_path, '../outro')), path)

@views.route("/api/outro_status")
def outro_status():
    from flask import jsonify
    status = _check_outro_timer()
    return jsonify(status)

@views.route("/api/outro_data")
def outro_data():
    """
    Dedicated endpoint that returns challenges, solves, and scoreboard data
    for the outro page. Bypass standard restrictions to show data after CTF ends.
    """
    from flask import jsonify
    from CTFd.models import Challenges as ChallengesModel
    from CTFd.utils.challenges import get_solves_for_challenge_id
    from CTFd.utils.modes import generate_account_url, get_mode_as_word, TEAMS_MODE
    from CTFd.utils.scores import get_standings, get_user_standings
    from collections import defaultdict
    from sqlalchemy import select

    # Only serve data when outro is enabled
    outro_enabled = str(get_config('outro_enabled') or 'disabled')
    if outro_enabled != 'enabled':
        abort(403)

    # Respect outro access control
    outro_access = str(get_config('outro_access') or 'authenticated')
    if outro_access == 'authenticated' and not authed():
        abort(403)
    elif outro_access == 'admins' and not is_admin():
        abort(403)

    # --- Challenges ---
    challs = ChallengesModel.query.filter(
        ChallengesModel.state != 'hidden',
        ChallengesModel.state != 'locked',
    ).order_by(ChallengesModel.value, ChallengesModel.id).all()

    challenges_list = []
    for c in challs:
        challenges_list.append({
            'id': c.id,
            'name': c.name,
            'value': c.value,
            'category': c.category,
            'type': c.type,
        })

    # --- Solves per challenge ---
    solves_map = {}
    for c in challs:
        solves_map[c.id] = get_solves_for_challenge_id(c.id)

    # --- Scoreboard ---
    standings = get_standings()
    mode = get_config("user_mode")
    account_type = get_mode_as_word()

    scoreboard = []
    if mode == TEAMS_MODE:
        r = db.session.execute(
            select(
                [
                    Users.id,
                    Users.name,
                    Users.oauth_id,
                    Users.team_id,
                    Users.hidden,
                    Users.banned,
                ]
            ).where(Users.team_id.isnot(None))
        )
        users_list = r.fetchall()
        membership = defaultdict(dict)
        for u in users_list:
            if u.hidden is False and u.banned is False:
                membership[u.team_id][u.id] = {
                    "id": u.id,
                    "oauth_id": u.oauth_id,
                    "name": u.name,
                    "score": 0,
                }
        user_standings = get_user_standings()
        for u in user_standings:
            if u.team_id in membership and u.user_id in membership[u.team_id]:
                membership[u.team_id][u.user_id]["score"] = int(u.score)

    for i, x in enumerate(standings):
        entry = {
            "pos": i + 1,
            "account_id": x.account_id,
            "account_url": generate_account_url(account_id=x.account_id),
            "account_type": account_type,
            "oauth_id": x.oauth_id,
            "name": x.name,
            "score": int(x.score),
        }
        if mode == TEAMS_MODE:
            entry["members"] = list(membership.get(x.account_id, {}).values())
        scoreboard.append(entry)

    return jsonify({
        'success': True,
        'challenges': challenges_list,
        'solves': solves_map,
        'scoreboard': scoreboard,
    })

@views.route("/outro")
def outro_page():
    outro_enabled = str(get_config('outro_enabled') or 'disabled')
    if outro_enabled != 'enabled':
        abort(404)

    outro_access = str(get_config('outro_access') or 'authenticated')
    if outro_access == 'authenticated' and not authed():
        return redirect(url_for("auth.login", next=request.full_path))
    elif outro_access == 'admins':
        if not is_admin():
            abort(403)

    outro_file = get_config('outro_file') or 'outro.html'
    outro_dir = os.path.abspath(os.path.join(app.root_path, '../outro'))
    return send_from_directory(outro_dir, outro_file)
