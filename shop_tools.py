from flask import Blueprint, render_template


def create_shop_tools_blueprint(*, access_required):
    """店舗メモツールの共通画面と将来機能の入口を提供する。"""
    blueprint = Blueprint("shop_tools", __name__)

    @blueprint.get("/shop-tools/memo")
    @access_required
    def memo_placeholder():
        return render_template(
            "shop_tools/placeholder.html",
            active_tool="memo",
            page_icon="📝",
            page_title="メモ",
            description="気づいたことをすぐ残せるメモ機能を準備中です。",
        )

    @blueprint.get("/shop-tools/tasks")
    @access_required
    def tasks_placeholder():
        return render_template(
            "shop_tools/placeholder.html",
            active_tool="tasks",
            page_icon="✅",
            page_title="タスク",
            description="やることを確認できるチェックリストを準備中です。",
        )

    return blueprint
