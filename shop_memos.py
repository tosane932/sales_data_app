import datetime
import logging
from zoneinfo import ZoneInfo

from flask import Blueprint, abort, redirect, render_template, request, url_for
from sqlalchemy.exc import SQLAlchemyError

from models import Dataset, ShopMemo, db, utc_now


logger = logging.getLogger(__name__)

SHOP_MEMO_LIMIT = 100
SHOP_MEMO_BODY_MAX_LENGTH = 2000
SHOP_MEMO_SEARCH_MAX_LENGTH = 100
SHOP_MEMO_DISPLAY_TIMEZONE = ZoneInfo("Asia/Tokyo")


def _validate_body(form):
    raw_body = form.get("body", "")
    body = raw_body.strip()

    if not body:
        return None, raw_body, "メモを入力してください。"
    if len(body) > SHOP_MEMO_BODY_MAX_LENGTH:
        return None, raw_body, "メモは2000文字以内で入力してください。"

    return body, raw_body, None


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


def create_shop_memos_blueprint(*, access_required, resolve_dataset):
    blueprint = Blueprint("shop_memos", __name__)

    def render_memos(
        current_dataset,
        *,
        deleted,
        form_value="",
        edit_form=None,
        error=None,
        status=200,
    ):
        search_query = request.args.get("q", "").strip()
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
                memo_query = memo_query.order_by(
                    ShopMemo.updated_at.desc(),
                    ShopMemo.id.desc(),
                )

            if search_query and not search_is_invalid:
                memo_query = memo_query.filter(
                    ShopMemo.body.ilike(
                        _literal_search_pattern(search_query),
                        escape="/",
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
                form_value=form_value,
                edit_form=edit_form,
                error=error,
                active_tool="memo",
                format_deleted_at=_format_deleted_at,
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

    @blueprint.get("/shop-tools/memo")
    @access_required
    def list_memos():
        current_dataset = resolve_dataset()
        return render_memos(current_dataset, deleted=False)

    @blueprint.post("/shop-tools/memo")
    @access_required
    def create_memo():
        current_dataset = resolve_dataset()
        body, raw_body, validation_error = _validate_body(request.form)
        if validation_error is not None:
            return render_memos(
                current_dataset,
                deleted=False,
                form_value=raw_body,
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
                    form_value=raw_body,
                    error=(
                        "メモはゴミ箱を含めて100件まで登録できます。"
                    ),
                    status=400,
                )

            db.session.add(
                ShopMemo(
                    dataset_id=locked_dataset.id,
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
                form_value=raw_body,
                error="メモを追加できませんでした。",
                status=500,
            )

        return redirect(url_for("shop_memos.list_memos"), code=303)

    @blueprint.post("/shop-tools/memo/<int:memo_id>/edit")
    @access_required
    def edit_memo(memo_id):
        current_dataset = resolve_dataset()
        body, raw_body, validation_error = _validate_body(request.form)
        if validation_error is not None:
            return render_memos(
                current_dataset,
                deleted=False,
                edit_form={"id": memo_id, "body": raw_body},
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

            memo.body = body
            memo.updated_at = utc_now()
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            logger.exception("Failed to edit a shop memo.")
            return render_memos(
                current_dataset,
                deleted=False,
                edit_form={"id": memo_id, "body": raw_body},
                error="メモを更新できませんでした。",
                status=500,
            )

        return redirect(url_for("shop_memos.list_memos"), code=303)

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

        return redirect(url_for("shop_memos.list_memos"), code=303)

    @blueprint.get("/shop-tools/memo/trash")
    @access_required
    def list_trash():
        current_dataset = resolve_dataset()
        return render_memos(current_dataset, deleted=True)

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
