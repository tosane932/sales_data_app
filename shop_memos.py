import datetime
import logging
import re
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from flask import (
    Blueprint,
    abort,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from markupsafe import Markup, escape
from sqlalchemy import or_
from sqlalchemy.exc import SQLAlchemyError

from models import Dataset, ShopMemo, db, utc_now


logger = logging.getLogger(__name__)

SHOP_MEMO_LIMIT = 100
SHOP_MEMO_TITLE_MAX_LENGTH = 100
SHOP_MEMO_BODY_MAX_LENGTH = 2000
SHOP_MEMO_SEARCH_MAX_LENGTH = 100
SHOP_MEMO_DISPLAY_TIMEZONE = ZoneInfo("Asia/Tokyo")
SHOP_MEMO_HTTP_URL_PATTERN = re.compile(
    r"https?://[^\s<>\"']+",
    re.IGNORECASE,
)
SHOP_MEMO_URL_TRAILING_PUNCTUATION = ".,!?;:、。！？；："
SHOP_MEMO_URL_CLOSING_DELIMITERS = {
    ")": "(",
    "]": "[",
    "}": "{",
    "）": "（",
    "］": "［",
    "｝": "｛",
    "〉": "〈",
    "》": "《",
    "」": "「",
    "』": "『",
    "】": "【",
}


def _validate_memo(form):
    raw_title = form.get("title", "")
    title = raw_title.strip()
    raw_body = form.get("body", "")
    body = raw_body.strip()

    if not title:
        return None, None, raw_title, raw_body, "タイトルを入力してください。"
    if len(title) > SHOP_MEMO_TITLE_MAX_LENGTH:
        return None, None, raw_title, raw_body, "タイトルは100文字以内で入力してください。"
    if not body:
        return None, None, raw_title, raw_body, "メモを入力してください。"
    if len(body) > SHOP_MEMO_BODY_MAX_LENGTH:
        return None, None, raw_title, raw_body, "メモは2000文字以内で入力してください。"

    return title, body, raw_title, raw_body, None


def _derive_title_from_body(body):
    for line in body.splitlines():
        stripped_line = line.strip()
        if stripped_line:
            return stripped_line[:SHOP_MEMO_TITLE_MAX_LENGTH]

    return body.strip()[:SHOP_MEMO_TITLE_MAX_LENGTH]


def _validate_autosave_body(payload):
    raw_body = payload.get("body", "") if isinstance(payload, dict) else ""
    if not isinstance(raw_body, str):
        return None, None, "メモの形式が正しくありません。"
    if not raw_body.strip():
        return None, None, "メモを入力してください。"
    if len(raw_body) > SHOP_MEMO_BODY_MAX_LENGTH:
        return None, None, "メモは2000文字以内で入力してください。"

    return _derive_title_from_body(raw_body), raw_body, None


def _literal_search_pattern(value):
    escaped_value = (
        value.replace("/", "//").replace("%", "/%").replace("_", "/_")
    )
    return f"%{escaped_value}%"


def _format_deleted_at(value):
    if value.tzinfo is None:
        value = value.replace(tzinfo=datetime.timezone.utc)
    return value.astimezone(SHOP_MEMO_DISPLAY_TIMEZONE).strftime(
        "%Y/%m/%d %H:%M"
    )


def _format_memo_date(value):
    if value.tzinfo is None:
        value = value.replace(tzinfo=datetime.timezone.utc)
    return value.astimezone(SHOP_MEMO_DISPLAY_TIMEZONE).strftime(
        "%Y/%m/%d"
    )


def _split_url_suffix(candidate):
    url = candidate
    suffix = ""

    while url:
        last_character = url[-1]
        if last_character in SHOP_MEMO_URL_TRAILING_PUNCTUATION:
            url = url[:-1]
            suffix = last_character + suffix
            continue

        opening_delimiter = SHOP_MEMO_URL_CLOSING_DELIMITERS.get(
            last_character
        )
        if (
            opening_delimiter is not None
            and url.count(last_character) > url.count(opening_delimiter)
        ):
            url = url[:-1]
            suffix = last_character + suffix
            continue

        break

    return url, suffix


def _is_linkable_http_url(candidate):
    try:
        parsed_url = urlsplit(candidate)
        hostname = parsed_url.hostname
    except ValueError:
        return False

    return (
        parsed_url.scheme.lower() in {"http", "https"}
        and bool(parsed_url.netloc)
        and bool(hostname)
    )


def _linkify_memo_body(value):
    """Escape memo text and promote only valid HTTP(S) URLs to anchors."""
    body = "" if value is None else str(value)
    fragments = []
    previous_end = 0

    for match in SHOP_MEMO_HTTP_URL_PATTERN.finditer(body):
        fragments.append(escape(body[previous_end:match.start()]))
        candidate, suffix = _split_url_suffix(match.group(0))

        if _is_linkable_http_url(candidate):
            escaped_url = escape(candidate)
            fragments.append(
                Markup('<a href="')
                + escaped_url
                + Markup(
                    '" target="_blank" '
                    'rel="noopener noreferrer">'
                )
                + escaped_url
                + Markup("</a>")
            )
            fragments.append(escape(suffix))
        else:
            fragments.append(escape(match.group(0)))

        previous_end = match.end()

    fragments.append(escape(body[previous_end:]))
    return Markup("").join(fragments)


def create_shop_memos_blueprint(*, access_required, resolve_dataset):
    blueprint = Blueprint("shop_memos", __name__)

    def render_memos(
        current_dataset,
        *,
        deleted,
        title_form_value="",
        form_value="",
        edit_form=None,
        error=None,
        status=200,
        create_form_open=False,
    ):
        search_query = request.args.get("q", "").strip()
        sort_by = request.args.get("sort", "updated")
        if sort_by not in {"updated", "created"}:
            sort_by = "updated"

        search_is_invalid = len(search_query) > SHOP_MEMO_SEARCH_MAX_LENGTH
        if search_is_invalid:
            error = "検索文字は100文字以内で入力してください。"
            status = 400

        try:
            memo_query = ShopMemo.query.filter_by(
                dataset_id=current_dataset.id,
            )
            if deleted:
                memo_query = memo_query.filter(ShopMemo.deleted_at.is_not(None))
                memo_query = memo_query.order_by(
                    ShopMemo.deleted_at.desc(),
                    ShopMemo.id.desc(),
                )
            else:
                memo_query = memo_query.filter(ShopMemo.deleted_at.is_(None))
                sort_column = (
                    ShopMemo.created_at
                    if sort_by == "created"
                    else ShopMemo.updated_at
                )
                memo_query = memo_query.order_by(
                    ShopMemo.pinned_at.is_(None).asc(),
                    ShopMemo.pinned_at.desc(),
                    sort_column.desc(),
                    ShopMemo.id.desc(),
                )

            if search_query and not search_is_invalid:
                search_pattern = _literal_search_pattern(search_query)
                memo_query = memo_query.filter(
                    or_(
                        ShopMemo.title.ilike(search_pattern, escape="/"),
                        ShopMemo.body.ilike(search_pattern, escape="/"),
                    )
                )

            memos = [] if search_is_invalid else memo_query.all()
            active_count = ShopMemo.query.filter_by(
                dataset_id=current_dataset.id,
                deleted_at=None,
            ).count()
            trash_count = (
                ShopMemo.query
                .filter_by(dataset_id=current_dataset.id)
                .filter(ShopMemo.deleted_at.is_not(None))
                .count()
            )
        except SQLAlchemyError:
            db.session.rollback()
            logger.exception("Failed to load shop memos.")
            return "メモを表示できませんでした。", 500

        template_name = (
            "shop_tools/memo_trash.html"
            if deleted
            else "shop_tools/memos.html"
        )
        return (
            render_template(
                template_name,
                memos=memos,
                active_count=active_count,
                trash_count=trash_count,
                search_query=search_query,
                sort_by=sort_by,
                title_form_value=title_form_value,
                form_value=form_value,
                edit_form=edit_form,
                error=error,
                create_form_open=create_form_open,
                active_tool="memo",
                format_deleted_at=_format_deleted_at,
                format_memo_date=_format_memo_date,
                linkify_memo_body=_linkify_memo_body,
            ),
            status,
        )

    def load_memo_for_update(current_dataset, memo_id, *, deleted):
        memo_query = ShopMemo.query.filter_by(
            id=memo_id,
            dataset_id=current_dataset.id,
        )
        if deleted:
            memo_query = memo_query.filter(ShopMemo.deleted_at.is_not(None))
        else:
            memo_query = memo_query.filter(ShopMemo.deleted_at.is_(None))

        return (
            memo_query
            .populate_existing()
            .with_for_update()
            .one_or_none()
        )

    def lock_current_dataset(current_dataset):
        return (
            Dataset.query
            .filter_by(
                id=current_dataset.id,
                kind=current_dataset.kind,
                system_key=current_dataset.system_key,
            )
            .populate_existing()
            .with_for_update()
            .one_or_none()
        )

    def memo_json_payload(memo):
        return {
            "id": memo.id,
            "title": memo.title,
            "body": memo.body,
            "pinned": memo.pinned_at is not None,
            "autosave_url": url_for(
                "shop_memos.autosave_memo",
                memo_id=memo.id,
            ),
            "pin_url": url_for("shop_memos.set_pin", memo_id=memo.id),
            "duplicate_url": url_for(
                "shop_memos.duplicate_memo",
                memo_id=memo.id,
            ),
            "trash_url": url_for(
                "shop_memos.move_to_trash",
                memo_id=memo.id,
            ),
            "restore_url": url_for(
                "shop_memos.restore_memo",
                memo_id=memo.id,
            ),
        }

    def json_error(message, status):
        return jsonify(ok=False, error=message), status

    @blueprint.get("/shop-tools/memo")
    @access_required
    def list_memos():
        current_dataset = resolve_dataset()
        return render_memos(current_dataset, deleted=False)

    @blueprint.post("/shop-tools/memo")
    @access_required
    def create_memo():
        current_dataset = resolve_dataset()
        title, body, raw_title, raw_body, validation_error = _validate_memo(
            request.form
        )
        if validation_error is not None:
            return render_memos(
                current_dataset,
                deleted=False,
                title_form_value=raw_title,
                form_value=raw_body,
                create_form_open=True,
                error=validation_error,
                status=400,
            )

        try:
            locked_dataset = (
                Dataset.query
                .filter_by(
                    id=current_dataset.id,
                    kind=current_dataset.kind,
                    system_key=current_dataset.system_key,
                )
                .populate_existing()
                .with_for_update()
                .one_or_none()
            )
            if locked_dataset is None:
                db.session.rollback()
                abort(403)

            memo_count = ShopMemo.query.filter_by(
                dataset_id=locked_dataset.id,
            ).count()
            if memo_count >= SHOP_MEMO_LIMIT:
                db.session.rollback()
                return render_memos(
                    current_dataset,
                    deleted=False,
                    title_form_value=raw_title,
                    form_value=raw_body,
                    create_form_open=True,
                    error=(
                        "メモはゴミ箱を含めて100件まで登録できます。"
                    ),
                    status=400,
                )

            db.session.add(
                ShopMemo(
                    dataset_id=locked_dataset.id,
                    title=title,
                    body=body,
                )
            )
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            logger.exception("Failed to create a shop memo.")
            return render_memos(
                current_dataset,
                deleted=False,
                title_form_value=raw_title,
                form_value=raw_body,
                create_form_open=True,
                error="メモを追加できませんでした。",
                status=500,
            )

        return redirect(url_for("shop_memos.list_memos"), code=303)

    @blueprint.post("/shop-tools/memo/autosave")
    @access_required
    def autosave_new_memo():
        current_dataset = resolve_dataset()
        title, body, validation_error = _validate_autosave_body(
            request.get_json(silent=True)
        )
        if validation_error is not None:
            return json_error(validation_error, 400)

        try:
            locked_dataset = lock_current_dataset(current_dataset)
            if locked_dataset is None:
                db.session.rollback()
                return json_error("メモを保存できませんでした。", 403)

            memo_count = ShopMemo.query.filter_by(
                dataset_id=locked_dataset.id,
            ).count()
            if memo_count >= SHOP_MEMO_LIMIT:
                db.session.rollback()
                return json_error(
                    "メモはゴミ箱を含めて100件まで登録できます。",
                    400,
                )

            memo = ShopMemo(
                dataset_id=locked_dataset.id,
                title=title,
                body=body,
            )
            db.session.add(memo)
            db.session.flush()
            memo_payload = memo_json_payload(memo)
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            logger.exception("Failed to autosave a new shop memo.")
            return json_error("保存できませんでした。", 500)

        return jsonify(ok=True, memo=memo_payload), 201

    @blueprint.post("/shop-tools/memo/<int:memo_id>/edit")
    @access_required
    def edit_memo(memo_id):
        current_dataset = resolve_dataset()
        title, body, raw_title, raw_body, validation_error = _validate_memo(
            request.form
        )
        if validation_error is not None:
            return render_memos(
                current_dataset,
                deleted=False,
                edit_form={
                    "id": memo_id,
                    "title": raw_title,
                    "body": raw_body,
                },
                error=validation_error,
                status=400,
            )

        try:
            memo = load_memo_for_update(
                current_dataset,
                memo_id,
                deleted=False,
            )
            if memo is None:
                abort(404)

            memo.title = title
            memo.body = body
            memo.updated_at = utc_now()
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            logger.exception("Failed to edit a shop memo.")
            return render_memos(
                current_dataset,
                deleted=False,
                edit_form={
                    "id": memo_id,
                    "title": raw_title,
                    "body": raw_body,
                },
                error="メモを更新できませんでした。",
                status=500,
            )

        return redirect(url_for("shop_memos.list_memos"), code=303)

    @blueprint.post("/shop-tools/memo/<int:memo_id>/autosave")
    @access_required
    def autosave_memo(memo_id):
        current_dataset = resolve_dataset()
        title, body, validation_error = _validate_autosave_body(
            request.get_json(silent=True)
        )
        if validation_error is not None:
            return json_error(validation_error, 400)

        try:
            memo = load_memo_for_update(
                current_dataset,
                memo_id,
                deleted=False,
            )
            if memo is None:
                abort(404)

            if memo.title != title or memo.body != body:
                memo.title = title
                memo.body = body
                memo.updated_at = utc_now()

            db.session.flush()
            memo_payload = memo_json_payload(memo)
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            logger.exception("Failed to autosave a shop memo.")
            return json_error("保存できませんでした。", 500)

        return jsonify(ok=True, memo=memo_payload)

    @blueprint.post("/shop-tools/memo/<int:memo_id>/pin")
    @access_required
    def set_pin(memo_id):
        current_dataset = resolve_dataset()
        payload = request.get_json(silent=True)
        desired_state = (
            payload.get("pinned") if isinstance(payload, dict) else None
        )
        if not isinstance(desired_state, bool):
            return json_error("ピン留め状態が正しくありません。", 400)

        try:
            memo = load_memo_for_update(
                current_dataset,
                memo_id,
                deleted=False,
            )
            if memo is None:
                abort(404)

            if desired_state and memo.pinned_at is None:
                memo.pinned_at = utc_now()
            elif not desired_state and memo.pinned_at is not None:
                memo.pinned_at = None

            db.session.flush()
            memo_payload = memo_json_payload(memo)
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            logger.exception("Failed to change a shop memo pin.")
            return json_error("ピン留めを変更できませんでした。", 500)

        return jsonify(ok=True, memo=memo_payload)

    @blueprint.post("/shop-tools/memo/<int:memo_id>/duplicate")
    @access_required
    def duplicate_memo(memo_id):
        current_dataset = resolve_dataset()

        try:
            locked_dataset = lock_current_dataset(current_dataset)
            if locked_dataset is None:
                db.session.rollback()
                return json_error("メモを複製できませんでした。", 403)

            source = load_memo_for_update(
                locked_dataset,
                memo_id,
                deleted=False,
            )
            if source is None:
                abort(404)

            memo_count = ShopMemo.query.filter_by(
                dataset_id=locked_dataset.id,
            ).count()
            if memo_count >= SHOP_MEMO_LIMIT:
                db.session.rollback()
                return json_error(
                    "メモはゴミ箱を含めて100件まで登録できます。",
                    400,
                )

            duplicate = ShopMemo(
                dataset_id=locked_dataset.id,
                title=source.title,
                body=source.body,
                pinned_at=None,
            )
            db.session.add(duplicate)
            db.session.flush()
            memo_payload = memo_json_payload(duplicate)
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            logger.exception("Failed to duplicate a shop memo.")
            return json_error("メモを複製できませんでした。", 500)

        return jsonify(ok=True, memo=memo_payload), 201

    @blueprint.post("/shop-tools/memo/<int:memo_id>/trash")
    @access_required
    def move_to_trash(memo_id):
        current_dataset = resolve_dataset()

        try:
            memo = load_memo_for_update(
                current_dataset,
                memo_id,
                deleted=False,
            )
            if memo is None:
                abort(404)

            deleted_at = utc_now()
            memo.deleted_at = deleted_at
            memo.updated_at = deleted_at
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            logger.exception("Failed to move a shop memo to trash.")
            return "メモをゴミ箱へ移動できませんでした。", 500

        if request.is_json:
            return jsonify(
                ok=True,
                memo_id=memo_id,
                restore_url=url_for(
                    "shop_memos.restore_memo",
                    memo_id=memo_id,
                ),
            )

        return redirect(url_for("shop_memos.list_memos"), code=303)

    @blueprint.get("/shop-tools/memo/trash")
    @access_required
    def list_trash():
        current_dataset = resolve_dataset()
        return render_memos(current_dataset, deleted=True)

    @blueprint.post("/shop-tools/memo/trash/empty")
    @access_required
    def empty_trash():
        current_dataset = resolve_dataset()

        try:
            deleted_count = (
                ShopMemo.query
                .filter_by(dataset_id=current_dataset.id)
                .filter(ShopMemo.deleted_at.is_not(None))
                .delete(synchronize_session=False)
            )
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            logger.exception("Failed to empty shop memo trash.")
            return "ゴミ箱を空にできませんでした。", 500

        if deleted_count:
            flash(
                f"ゴミ箱のメモを{deleted_count}件完全に削除しました。",
                "success",
            )
        else:
            flash("ゴミ箱はすでに空です。", "success")

        return redirect(url_for("shop_memos.list_trash"), code=303)

    @blueprint.post("/shop-tools/memo/<int:memo_id>/restore")
    @access_required
    def restore_memo(memo_id):
        current_dataset = resolve_dataset()

        try:
            memo = load_memo_for_update(
                current_dataset,
                memo_id,
                deleted=True,
            )
            if memo is None:
                abort(404)

            memo.deleted_at = None
            memo.updated_at = utc_now()
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            logger.exception("Failed to restore a shop memo.")
            return "メモを復元できませんでした。", 500

        if request.is_json:
            return jsonify(ok=True, memo=memo_json_payload(memo))

        return redirect(url_for("shop_memos.list_memos"), code=303)

    @blueprint.post("/shop-tools/memo/<int:memo_id>/delete")
    @access_required
    def delete_memo(memo_id):
        current_dataset = resolve_dataset()

        try:
            memo = load_memo_for_update(
                current_dataset,
                memo_id,
                deleted=True,
            )
            if memo is None:
                abort(404)

            db.session.delete(memo)
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            logger.exception("Failed to permanently delete a shop memo.")
            return "メモを完全に削除できませんでした。", 500

        return redirect(url_for("shop_memos.list_trash"), code=303)

    return blueprint
