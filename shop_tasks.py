import logging

from flask import (
    Blueprint,
    abort,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from sqlalchemy.exc import SQLAlchemyError

from models import Dataset, ShopTask, db, utc_now


logger = logging.getLogger(__name__)

SHOP_TASK_LIMIT = 100
SHOP_TASK_TITLE_MAX_LENGTH = 100


def _validate_title(form):
    raw_title = form.get("title", "")
    if not isinstance(raw_title, str):
        return None, "", "タスク名を入力してください。"
    title = raw_title.strip()

    if not title:
        return None, raw_title, "タスク名を入力してください。"
    if len(title) > SHOP_TASK_TITLE_MAX_LENGTH:
        return None, raw_title, "タスク名は100文字以内で入力してください。"

    return title, raw_title, None


def _parse_boolean(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value in {"0", "1"}:
        return value == "1"
    return None


def _parse_task_ids(payload):
    raw_ids = payload.get("task_ids") if isinstance(payload, dict) else None
    if not isinstance(raw_ids, list) or not raw_ids:
        return None
    if any(
        isinstance(task_id, bool) or not isinstance(task_id, int)
        for task_id in raw_ids
    ):
        return None
    task_ids = list(raw_ids)
    if any(task_id <= 0 for task_id in task_ids):
        return None
    if len(task_ids) != len(set(task_ids)):
        return None
    return task_ids


def create_shop_tasks_blueprint(*, access_required, resolve_dataset):
    blueprint = Blueprint("shop_tasks", __name__)

    def task_json_payload(task):
        return {
            "id": task.id,
            "title": task.title,
            "is_completed": task.is_completed,
            "is_starred": task.is_starred,
            "position": task.position,
        }

    def json_error(message, status):
        return jsonify(ok=False, error=message), status

    def request_payload():
        if request.is_json:
            payload = request.get_json(silent=True)
            return payload if isinstance(payload, dict) else {}
        return request.form

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

    def load_task_for_update(current_dataset, task_id):
        return (
            ShopTask.query
            .filter_by(
                id=task_id,
                dataset_id=current_dataset.id,
            )
            .populate_existing()
            .with_for_update()
            .one_or_none()
        )

    def render_list(current_dataset, *, title_value="", error=None, status=200):
        try:
            tasks = (
                ShopTask.query
                .filter_by(dataset_id=current_dataset.id)
                .order_by(
                    ShopTask.is_completed.asc(),
                    ShopTask.position.asc(),
                    ShopTask.id.asc(),
                )
                .all()
            )
        except SQLAlchemyError:
            db.session.rollback()
            logger.exception("Failed to load shop tasks.")
            return "タスクを表示できませんでした。", 500

        incomplete_tasks = [task for task in tasks if not task.is_completed]
        completed_tasks = [task for task in tasks if task.is_completed]
        return (
            render_template(
                "shop_tasks.html",
                incomplete_tasks=incomplete_tasks,
                completed_tasks=completed_tasks,
                title_value=title_value,
                error=error,
                active_tool="tasks",
            ),
            status,
        )

    @blueprint.get("/shop-tools/tasks")
    @access_required
    def list_tasks():
        current_dataset = resolve_dataset()
        return render_list(current_dataset)

    @blueprint.post("/shop-tools/tasks")
    @access_required
    def create_task():
        current_dataset = resolve_dataset()
        payload = request_payload()
        title, raw_title, validation_error = _validate_title(payload)

        if validation_error is not None:
            if request.is_json:
                return json_error(validation_error, 400)

            return render_list(
                current_dataset,
                title_value=raw_title,
                error=validation_error,
                status=400,
            )

        try:
            locked_dataset = lock_current_dataset(current_dataset)
            if locked_dataset is None:
                db.session.rollback()
                abort(403)

            current_task_count = ShopTask.query.filter_by(
                dataset_id=locked_dataset.id,
            ).count()

            if current_task_count >= SHOP_TASK_LIMIT:
                db.session.rollback()

                if request.is_json:
                    return json_error(
                        "タスクは100件まで登録できます。",
                        400,
                    )

                return render_list(
                    current_dataset,
                    title_value=raw_title,
                    error="タスクは100件まで登録できます。",
                    status=400,
                )

            ShopTask.query.filter_by(
                dataset_id=locked_dataset.id,
            ).update(
                {ShopTask.position: ShopTask.position + 1},
                synchronize_session=False,
            )

            task = ShopTask(
                dataset_id=locked_dataset.id,
                title=title,
                position=0,
            )
            db.session.add(task)
            db.session.commit()

        except SQLAlchemyError:
            db.session.rollback()
            logger.exception("Failed to create a shop task.")

            if request.is_json:
                return json_error(
                    "タスクを追加できませんでした。",
                    500,
                )

            return render_list(
                current_dataset,
                title_value=raw_title,
                error="タスクを追加できませんでした。",
                status=500,
            )

        if request.is_json:
            return {
                "ok": True,
                "task": {
                    "id": task.id,
                    "title": task.title,
                    "position": task.position,
                    "is_completed": task.is_completed,
                    "is_starred": task.is_starred,
                    "completion_url": url_for(
                        "shop_tasks.set_completion",
                        task_id=task.id,
                    ),
                    "edit_url": url_for(
                        "shop_tasks.edit_task",
                        task_id=task.id,
                    ),
                    "favorite_url": url_for(
                        "shop_tasks.set_favorite",
                        task_id=task.id,
                    ),
                }
            }, 201

        return redirect(url_for("shop_tasks.list_tasks"), code=303)


    @blueprint.post("/shop-tools/tasks/<int:task_id>/completion")
    @access_required
    def set_completion(task_id):
        current_dataset = resolve_dataset()
        desired_state = _parse_boolean(request_payload().get("completed"))
        if desired_state is None:
            if request.is_json:
                return json_error("完了状態が正しくありません。", 400)
            return "完了状態が正しくありません。", 400

        try:
            task = load_task_for_update(current_dataset, task_id)
            if task is None:
                abort(404)

            should_be_completed = desired_state
            if should_be_completed and not task.is_completed:
                task.is_completed = True
                task.completed_at = utc_now()
            elif not should_be_completed and task.is_completed:
                task.is_completed = False
                task.completed_at = None

            db.session.flush()
            payload = task_json_payload(task)
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            logger.exception("Failed to update a shop task.")
            if request.is_json:
                return json_error("タスクの完了状態を更新できませんでした。", 500)
            return "タスクの完了状態を更新できませんでした。", 500

        if request.is_json:
            return jsonify(ok=True, task=payload)
        return redirect(url_for("shop_tasks.list_tasks"), code=303)

    @blueprint.post("/shop-tools/tasks/<int:task_id>/delete")
    @access_required
    def delete_task(task_id):
        current_dataset = resolve_dataset()

        try:
            task = load_task_for_update(current_dataset, task_id)
            if task is None:
                abort(404)

            db.session.delete(task)
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            logger.exception("Failed to delete a shop task.")
            return "タスクを削除できませんでした。", 500

        return redirect(url_for("shop_tasks.list_tasks"), code=303)

    @blueprint.post("/shop-tools/tasks/<int:task_id>/edit")
    @access_required
    def edit_task(task_id):
        current_dataset = resolve_dataset()
        payload = request_payload()
        title, raw_title, validation_error = _validate_title(payload)
        if validation_error is not None:
            if request.is_json:
                return json_error(validation_error, 400)
            return render_list(
                current_dataset,
                title_value=raw_title,
                error=validation_error,
                status=400,
            )

        try:
            task = load_task_for_update(current_dataset, task_id)
            if task is None:
                abort(404)
            task.title = title
            db.session.flush()
            response_payload = task_json_payload(task)
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            logger.exception("Failed to edit a shop task.")
            if request.is_json:
                return json_error("タスクを更新できませんでした。", 500)
            return render_list(
                current_dataset,
                title_value=raw_title,
                error="タスクを更新できませんでした。",
                status=500,
            )

        if request.is_json:
            return jsonify(ok=True, task=response_payload)
        return redirect(url_for("shop_tasks.list_tasks"), code=303)

    @blueprint.post("/shop-tools/tasks/<int:task_id>/favorite")
    @access_required
    def set_favorite(task_id):
        current_dataset = resolve_dataset()
        desired_state = _parse_boolean(request_payload().get("starred"))
        if desired_state is None:
            if request.is_json:
                return json_error("お気に入り状態が正しくありません。", 400)
            return "お気に入り状態が正しくありません。", 400

        try:
            task = load_task_for_update(current_dataset, task_id)
            if task is None:
                abort(404)
            task.is_starred = desired_state
            db.session.flush()
            response_payload = task_json_payload(task)
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            logger.exception("Failed to update a shop task favorite.")
            if request.is_json:
                return json_error("お気に入りを更新できませんでした。", 500)
            return "お気に入りを更新できませんでした。", 500

        if request.is_json:
            return jsonify(ok=True, task=response_payload)
        return redirect(url_for("shop_tasks.list_tasks"), code=303)

    @blueprint.post("/shop-tools/tasks/reorder")
    @access_required
    def reorder_tasks():
        current_dataset = resolve_dataset()
        task_ids = _parse_task_ids(request.get_json(silent=True))
        if task_ids is None:
            return json_error("並び順が正しくありません。", 400)

        try:
            locked_dataset = lock_current_dataset(current_dataset)
            if locked_dataset is None:
                db.session.rollback()
                return json_error("並び順を保存できませんでした。", 403)
            tasks = (
                ShopTask.query
                .filter_by(dataset_id=locked_dataset.id)
                .populate_existing()
                .with_for_update()
                .all()
            )
            tasks_by_id = {task.id: task for task in tasks}
            if len(task_ids) != len(tasks) or set(task_ids) != set(tasks_by_id):
                db.session.rollback()
                return json_error("並び順が正しくありません。", 400)
            for position, task_id in enumerate(task_ids):
                tasks_by_id[task_id].position = position
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            logger.exception("Failed to reorder shop tasks.")
            return json_error("並び順を保存できませんでした。", 500)

        return jsonify(ok=True, task_ids=task_ids)

    @blueprint.post("/shop-tools/tasks/bulk-delete")
    @access_required
    def bulk_delete_tasks():
        current_dataset = resolve_dataset()
        task_ids = _parse_task_ids(request.get_json(silent=True))
        if task_ids is None:
            return json_error("削除対象が正しくありません。", 400)

        try:
            tasks = (
                ShopTask.query
                .filter(
                    ShopTask.dataset_id == current_dataset.id,
                    ShopTask.id.in_(task_ids),
                )
                .populate_existing()
                .with_for_update()
                .all()
            )
            if len(tasks) != len(task_ids):
                db.session.rollback()
                return json_error("削除対象が正しくありません。", 400)
            for task in tasks:
                db.session.delete(task)
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            logger.exception("Failed to bulk delete shop tasks.")
            return json_error("選択したタスクを削除できませんでした。", 500)

        return jsonify(ok=True, deleted_ids=task_ids)

    @blueprint.post("/shop-tools/tasks/completed/delete")
    @access_required
    def delete_completed_tasks():
        current_dataset = resolve_dataset()

        try:
            completed_tasks = (
                ShopTask.query
                .filter_by(
                    dataset_id=current_dataset.id,
                    is_completed=True,
                )
                .populate_existing()
                .with_for_update()
                .all()
            )
            for task in completed_tasks:
                db.session.delete(task)
            deleted_count = len(completed_tasks)
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            logger.exception("Failed to delete completed shop tasks.")
            if request.is_json:
                return json_error("完了済みタスクを削除できませんでした。", 500)
            return "完了済みタスクを削除できませんでした。", 500

        if request.is_json:
            return jsonify(ok=True, deleted_count=deleted_count)
        return redirect(url_for("shop_tasks.list_tasks"), code=303)

    return blueprint
