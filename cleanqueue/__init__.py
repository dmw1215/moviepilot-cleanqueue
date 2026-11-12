"""
清理队列与白名单外数据

对应用户脚本：
- 清除MoviePilot整理队列.sh：仅清空 transferpending 待整理队列及其关联的 transferexecutionstep 记录。
- 精简MoviePilot配置.sh：清理白名单以外的全部业务数据（保留用户/设置/站点/插件核心表），
  VACUUM + 完整性检查。注意：自定义工作流（workflow）不在白名单内，会被清空。

与脚本不同：插件在应用运行时直接操作（不停容器），每次操作前自动在线备份数据库，
失败自动回滚。仅操作数据库，不动配置文件与插件目录。

安装位置：/app/app/plugins/cleanqueue/__init__.py
持久副本：/config/plugins-local/cleanqueue/__init__.py（容器重建后复制回去即可恢复）
"""

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import text

from app.db.base import DbOper
from app.db.oper.transferpending import TransferPendingOper
from app.plugins import _PluginBase
from app.sdk.config import settings
from app.sdk.logging import logger

# 白名单：清理白名单以外的数据时保留的核心表（与 精简MoviePilot配置.sh 保持一致）
KEEP_TABLES = {
    # 用户与设置
    "user", "userconfig", "systemconfig",
    # 站点配置
    "site", "siteicon", "siteuserdata", "sitestatistic",
    # 插件数据
    "plugindata", "pluginidentity", "plugininstallation", "plugininstance",
    # 其他必要配置
    "passkey", "alembic_version",
}

# 白名单展示（表名 + 说明）
KEEP_TABLES_DESC = [
    ("user", "管理员账号、密码、权限"),
    ("userconfig", "用户个人设置"),
    ("systemconfig", "系统全局设置"),
    ("site", "PT 站点、Cookie、认证信息"),
    ("plugindata", "插件配置和数据"),
    ("pluginidentity", "插件身份信息"),
    ("plugininstallation", "插件安装记录"),
    ("plugininstance", "插件实例和配置"),
    ("passkey", "通行密钥"),
    ("alembic_version", "数据库版本"),
    ("siteicon", "站点图标"),
    ("sitestatistic", "站点统计"),
    ("siteuserdata", "站点用户统计"),
]

# 将清理的主要业务表展示（表名 + 说明）
CLEAR_TABLES_DESC = [
    ("agentchat", "AI 对话历史"),
    ("agentinvocation", "AI 调用记录"),
    ("agenttaskrun", "AI 任务执行历史"),
    ("agenttask", "AI 任务及定时任务配置"),
    ("downloadhistory", "下载历史"),
    ("downloadfiles", "下载文件索引"),
    ("transferhistory", "媒体整理历史"),
    ("mediaserveritem", "媒体服务器索引"),
    ("subscribehistory", "订阅历史"),
    ("message", "消息历史"),
    ("workflow", "自定义工作流"),
    ("subscribe", "订阅任务"),
    ("transferpending", "待整理任务"),
    ("downloadfailure", "下载失败记录"),
]


class CleanQueue(_PluginBase):
    plugin_name = "清理队列与白名单外数据"
    plugin_desc = "一键清理待整理队列；清理白名单以外的全部业务数据（保留用户/设置/站点/插件数据）。每次操作前自动备份数据库并做完整性检查。"
    plugin_icon = "clean.png"
    plugin_version = "1.2.0"
    plugin_author = "噜噜"
    plugin_config_prefix = "cleanqueue_"
    plugin_order = 50
    auth_level = 2

    _enabled = False

    def init_plugin(self, config: Optional[Dict[str, Any]] = None) -> None:
        self._enabled = True

    def get_state(self) -> bool:
        return True

    def get_service(self) -> List[Dict[str, Any]]:
        return []

    def stop_service(self) -> None:
        pass

    def get_form(self) -> Tuple[Optional[List[Dict[str, Any]]], Dict[str, Any]]:
        return [{
            "component": "VForm",
            "content": [{
                "component": "VRow",
                "content": [{
                    "component": "VCol",
                    "props": {"cols": 12},
                    "content": [{
                        "component": "VAlert",
                        "props": {
                            "type": "info",
                            "variant": "tonal",
                            "text": "本插件无需配置，打开详情页即可操作。两个操作都会先自动备份数据库，失败自动回滚。",
                        },
                    }],
                }],
            }],
        }], {}

    # ---------------- 内部工具 ----------------

    def _config_dir(self) -> Path:
        cfg = getattr(settings, "CONFIG_PATH", None)
        if cfg:
            p = Path(str(cfg))
            if (p / "user.db").exists() or (p / "moviepilot.db").exists():
                return p
        return Path("/config")

    def _find_db(self) -> Optional[Path]:
        cfg = self._config_dir()
        for name in ("user.db", "moviepilot.db"):
            p = cfg / name
            if not p.is_file():
                continue
            try:
                conn = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=10)
                try:
                    row = conn.execute(
                        "SELECT 1 FROM sqlite_master "
                        "WHERE type='table' AND name='transferpending'"
                    ).fetchone()
                finally:
                    conn.close()
                if row:
                    return p
            except Exception as e:
                logger.warning(f"检测数据库 {p} 失败：{e}")
        p = cfg / "user.db"
        return p if p.is_file() else None

    def _backup_db(self, db_path: Path, tag: str) -> str:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        if tag == "slim":
            backup_dir = Path("/cache/Backup/MoviePilot")
            backup_dir.mkdir(parents=True, exist_ok=True)
            backup = backup_dir / f"MoviePilot-精简前完整备份-{stamp}.db"
        else:
            backup = Path(f"{db_path}.整理队列清理前-{stamp}")
        src = sqlite3.connect(str(db_path), timeout=60)
        try:
            dst = sqlite3.connect(str(backup))
            try:
                src.backup(dst)
                result = dst.execute("PRAGMA quick_check").fetchone()[0]
                if result != "ok":
                    raise RuntimeError(f"备份数据库完整性检查失败：{result}")
            finally:
                dst.close()
        finally:
            src.close()
        return str(backup)

    def _queue_counts(self) -> Tuple[int, int]:
        oper = TransferPendingOper()

        def _q(session):
            pending = session.execute(
                text('SELECT COUNT(*) FROM "transferpending"')).scalar() or 0
            steps = session.execute(
                text('SELECT COUNT(*) FROM "transferexecutionstep"')).scalar() or 0
            return pending, steps

        try:
            return oper._execute_sync_query(_q)
        except Exception as e:
            logger.warning(f"查询整理队列数量失败：{e}")
            return 0, 0

    def _other_clear_tables(self) -> List[str]:
        """除白名单与已列出的 14 张表外，其他将被清理的表（实时查询）。"""
        try:
            db_path = self._find_db()
            if not db_path:
                return []
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=10)
            try:
                tables = [r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                ).fetchall()]
            finally:
                conn.close()
            clear_names = {name for name, _ in CLEAR_TABLES_DESC}
            return [t for t in tables if t not in KEEP_TABLES and t not in clear_names]
        except Exception as e:
            logger.warning(f"查询其他待清理表失败：{e}")
            return []

    def _record_history(self, action: str, message: str) -> None:
        try:
            history = self.get_data("history") or []
            history.insert(0, {
                "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "action": action,
                "message": message,
            })
            self.save_data("history", history[:10])
        except Exception as e:
            logger.warning(f"保存操作记录失败：{e}")

    def _do_clear_queue(self) -> Dict[str, Any]:
        db_path = self._find_db()
        if not db_path:
            raise RuntimeError("没有找到 MoviePilot 数据库")
        backup = self._backup_db(db_path, "queue")

        oper = TransferPendingOper()

        def _tx(session):
            child = "transferexecutionstep"
            fks = session.execute(
                text(f'PRAGMA foreign_key_list("{child}")')).fetchall()
            groups: Dict[int, list] = {}
            for fk in fks:
                if str(fk[2]).lower() == "transferpending":
                    groups.setdefault(fk[0], []).append(fk)
            parent_info = session.execute(
                text('PRAGMA table_info("transferpending")')).fetchall()
            parent_pk = [r[1] for r in sorted(
                [r for r in parent_info if r[5] > 0], key=lambda r: r[5])]
            step_count = 0
            for group in groups.values():
                group.sort(key=lambda r: r[1])
                conditions = []
                for fk in group:
                    child_col, parent_col = fk[3], fk[4]
                    if not parent_col:
                        seq = fk[1]
                        if seq >= len(parent_pk):
                            raise RuntimeError("无法确定外键目标列")
                        parent_col = parent_pk[seq]
                    conditions.append(
                        f'"{child}"."{child_col}" = p."{parent_col}"')
                sql = ('DELETE FROM "' + child + '" WHERE EXISTS '
                       '(SELECT 1 FROM "transferpending" AS p WHERE '
                       + " AND ".join(conditions) + ")")
                cur = session.execute(text(sql))
                step_count += max(cur.rowcount or 0, 0)
            pending = session.execute(
                text('SELECT COUNT(*) FROM "transferpending"')).scalar() or 0
            session.execute(text('DELETE FROM "transferpending"'))
            remaining = session.execute(
                text('SELECT COUNT(*) FROM "transferpending"')).scalar() or 0
            if remaining != 0:
                raise RuntimeError("待整理队列未清空")
            violations = session.execute(
                text("PRAGMA foreign_key_check")).fetchall()
            if violations:
                raise RuntimeError(f"外键检查失败，发现 {len(violations)} 条异常")
            return pending, step_count

        pending, step_count = oper._execute_sync_write(_tx)
        msg = (f"整理队列已清理：待整理 {pending} 条，关联执行步骤 {step_count} 条；"
               f"备份：{backup}")
        self._record_history("清理整理队列", msg)
        logger.info(msg)
        return {"success": True, "message": msg,
                "pending": pending, "steps": step_count}

    def _do_slim(self, vacuum: bool = True) -> Dict[str, Any]:
        db_path = self._find_db()
        if not db_path:
            raise RuntimeError("没有找到 MoviePilot 数据库")
        backup = self._backup_db(db_path, "slim")

        oper = DbOper()

        def _tx(session):
            session.execute(text("PRAGMA foreign_keys=OFF"))
            tables = [r[0] for r in session.execute(text(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )).fetchall()]
            cleared: Dict[str, int] = {}
            for name in tables:
                if name in KEEP_TABLES:
                    continue
                cur = session.execute(text(f'DELETE FROM "{name}"'))
                cleared[name] = max(cur.rowcount or 0, 0)
            return cleared

        cleared = oper._execute_sync_write(_tx)
        total = sum(cleared.values())
        vacuum_ok = True
        vacuum_err = ""
        if vacuum:
            try:
                conn = sqlite3.connect(str(db_path), timeout=60)
                try:
                    conn.isolation_level = None
                    conn.execute("VACUUM")
                finally:
                    conn.close()
            except Exception as e:
                vacuum_ok = False
                vacuum_err = str(e)
                logger.warning(f"VACUUM 失败（数据已清空，仅空间未回收）：{e}")
        conn = sqlite3.connect(str(db_path), timeout=60)
        try:
            check = conn.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            conn.close()
        if check != "ok":
            raise RuntimeError(f"数据库完整性检查失败：{check}")
        kept = sorted(KEEP_TABLES)
        vacuum_text = "已跳过" if not vacuum else ("成功" if vacuum_ok else "失败（" + vacuum_err + "）")
        msg = (f"清理白名单以外的数据完成：清空 {len(cleared)} 张业务表、共 {total} 条记录；"
               f"完整性：{check}；VACUUM：{vacuum_text}；"
               f"保留表：{', '.join(kept)}；备份：{backup}")
        self._record_history("清理白名单以外的数据", msg)
        logger.info(msg)
        return {"success": True, "message": msg,
                "cleared_tables": len(cleared), "cleared_rows": total,
                "integrity": check, "vacuum_ok": vacuum_ok}

    # ---------------- 插件 API ----------------

    def get_api(self) -> List[Dict[str, Any]]:
        return [
            {"path": "/status", "endpoint": self.api_status,
             "methods": ["GET"], "auth": "bear", "summary": "查询整理队列数量"},
            {"path": "/clear_queue", "endpoint": self.api_clear_queue,
             "methods": ["POST"], "auth": "bear", "summary": "清理整理队列"},
            {"path": "/slim_config", "endpoint": self.api_slim_config,
             "methods": ["POST"], "auth": "bear", "summary": "清理白名单以外的数据"},
        ]

    def api_status(self):
        pending, steps = self._queue_counts()
        return {"success": True, "pending": pending, "steps": steps,
                "message": f"待整理队列 {pending} 条，关联执行步骤 {steps} 条"}

    def api_clear_queue(self):
        try:
            return self._do_clear_queue()
        except Exception as e:
            logger.error(f"清理整理队列失败：{e}", exc_info=True)
            return {"success": False, "message": f"清理失败：{e}"}

    def api_slim_config(self, payload: Optional[Dict[str, Any]] = None):
        try:
            vacuum = True
            if payload:
                vacuum = payload.get("vacuum", True) is not False
            return self._do_slim(vacuum=vacuum)
        except Exception as e:
            logger.error(f"清理白名单以外的数据失败：{e}", exc_info=True)
            return {"success": False, "message": f"清理失败：{e}"}

    # ---------------- 插件页面 ----------------

    def get_page(self) -> List[dict]:
        pending, steps = self._queue_counts()
        other_tables = self._other_clear_tables()
        history = []
        try:
            history = self.get_data("history") or []
        except Exception:
            history = []
        history_lines = []
        for h in history[:5]:
            history_lines.append(
                f"{h.get('time', '')} {h.get('action', '')}：{h.get('message', '')}")

        keep_lines = [f"{name}：{desc}" for name, desc in KEEP_TABLES_DESC]
        clear_lines = [f"{name}：{desc}" for name, desc in CLEAR_TABLES_DESC]
        if other_tables:
            clear_lines.append("其他不在白名单中的表：" + "、".join(sorted(other_tables)))

        return [
            {"component": "VAlert", "props": {
                "type": "info", "variant": "tonal",
                "text": "两个操作都会先自动备份数据库，失败自动回滚。",
            }},
            # ---- 功能一：清理整理队列 ----
            {"component": "VCard", "props": {"class": "mb-4"}, "content": [
                {"component": "VCardText",
                 "props": {"class": "text-h6 font-weight-bold"},
                 "text": "一、清理整理队列"},
                {"component": "VCardText", "props": {"class": "pa-2"},
                 "text": f"当前待整理队列：{pending} 条（关联执行步骤 {steps} 条）"},
                {"component": "VRow", "content": [
                    {"component": "VCol", "props": {"cols": 12, "md": 6}, "content": [
                        {"component": "VSwitch", "props": {
                            "model": "confirm_queue",
                            "label": "确认清理整理队列",
                        }},
                    ]},
                    {"component": "VCol", "props": {"cols": 12, "md": 6}, "content": [
                        {"component": "VBtn", "props": {
                            "color": "error", "variant": "flat", "block": True,
                            "prepend-icon": "mdi-broom",
                            "onclick": "function(e) { "
                                       "if (!model.confirm_queue) { alert('请先打开「确认清理整理队列」开关'); return; } "
                                       "if (!confirm('将清空全部待整理队列及关联执行步骤，操作前会自动备份数据库。是否继续？')) return; "
                                       "window.MoviePilotAPI.post('plugin/CleanQueue/clear_queue', {})"
                                       ".then(function(r) { alert(r && r.message ? r.message : '清理完成'); location.reload(); })"
                                       ".catch(function(err) { console.error(err); alert('清理失败，请查看日志') }) }",
                        }, "text": "清理整理队列"},
                    ]},
                ]},
                {"component": "VCardText", "props": {"class": "text-caption pa-2"},
                 "text": "注释：只清空待整理队列及关联执行步骤（transferpending 及其关联的 "
                         "transferexecutionstep 记录），不影响整理历史、订阅、下载记录等其他数据。"},
            ]},
            # ---- 功能二：清理白名单以外的数据 ----
            {"component": "VCard", "props": {"class": "mb-4"}, "content": [
                {"component": "VCardText",
                 "props": {"class": "text-h6 font-weight-bold"},
                 "text": "二、清理白名单以外的数据"},
                {"component": "VAlert", "props": {
                    "type": "error", "variant": "tonal",
                    "text": "危险操作：这会清空白名单以外的全部业务数据，包括整理历史、订阅、下载记录、"
                            "自定义工作流（workflow 不在白名单内，会被清空），不只是整理队列！"
                            "删除后无法恢复，只能从备份文件手动恢复数据库。执行前请确认这些数据确实不需要了。",
                }},
                {"component": "VCardText", "props": {"class": "pa-2"},
                 "text": "白名单（保留以下 13 张表）：" + "；".join(keep_lines)},
                {"component": "VCardText", "props": {"class": "pa-2"},
                 "text": "将清理以下表：" + "；".join(clear_lines)},
                {"component": "VRow", "content": [
                    {"component": "VCol", "props": {"cols": 12, "md": 6}, "content": [
                        {"component": "VSwitch", "props": {
                            "model": "confirm_slim",
                            "label": "确认清理白名单以外的数据",
                        }},
                    ]},
                    {"component": "VCol", "props": {"cols": 12, "md": 6}, "content": [
                        {"component": "VSwitch", "props": {
                            "model": "slim_vacuum",
                            "label": "执行后压缩数据库（VACUUM）",
                        }},
                    ]},
                ]},
                {"component": "VRow", "content": [
                    {"component": "VCol", "props": {"cols": 12}, "content": [
                        {"component": "VBtn", "props": {
                            "color": "error", "variant": "tonal", "block": True,
                            "prepend-icon": "mdi-database-off",
                            "onclick": "function(e) { "
                                       "if (!model.confirm_slim) { alert('请先打开「确认清理白名单以外的数据」开关'); return; } "
                                       "if (!confirm('将清空白名单以外的全部业务数据，包括整理历史、订阅、下载记录、自定义工作流等，不只是整理队列！删除后无法恢复，只能从备份手动恢复。操作前会自动备份数据库。是否继续？')) return; "
                                       "var vacuum = (model.slim_vacuum === false) ? false : true; "
                                       "window.MoviePilotAPI.post('plugin/CleanQueue/slim_config', {vacuum: vacuum})"
                                       ".then(function(r) { alert(r && r.message ? r.message : '清理完成'); location.reload(); })"
                                       ".catch(function(err) { console.error(err); alert('清理失败，请查看日志') }) }",
                        }, "text": "清理白名单以外的数据"},
                    ]},
                ]},
                {"component": "VCardText", "props": {"class": "text-caption pa-2"},
                 "text": "注释：清空白名单以外的全部业务数据，不只是整理队列；仅操作数据库，"
                         "不动配置文件与插件目录（app.env、plugins/、local_plugins/、"
                         "plugins_backup/、plugin_transactions/）。"
                         "删除后无法恢复，只能从备份手动恢复。"},
            ]},
            {"component": "VCardText", "props": {"class": "pa-2"},
             "text": ("最近操作记录：" + "；".join(history_lines)) if history_lines else "最近操作记录：暂无"},
        ]
