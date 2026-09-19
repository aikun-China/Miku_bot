"""
MikuBot 数据库模块
==================
提供 SQLite 数据库的基础操作，支持：
- 同步/异步操作
- 连接池管理
- 事务支持
- 基础ORM功能

使用示例：
```python
from utils.database import Database, Model, Field

class User(Model):
    table_name = "users"
    fields = {
        "user_id": Field(str, primary_key=True),
        "gold": Field(int, default=100),
        "props": Field(str, default="{}"),
    }

# 获取数据库实例
db = await Database.get_instance()

# 基础操作
await db.execute("INSERT INTO users (user_id, gold) VALUES (?, ?)", (uid, 100))
row = await db.fetchone("SELECT * FROM users WHERE user_id = ?", (uid,))
await db.fetchall("SELECT * FROM users")

# 事务操作
async with db.transaction():
    await db.execute("UPDATE users SET gold = gold - ? WHERE user_id = ?", (10, uid))
    await db.execute("INSERT INTO logs (user_id, action) VALUES (?, ?)", (uid, "buy"))
```

迁移脚本：
```python
# 在 Model.init_tables() 中定义
@classmethod
def get_migrations(cls) -> list[str]:
    return [
        "ALTER TABLE users ADD COLUMN new_field TEXT",
    ]
```
"""

import sqlite3
import asyncio
import threading
import json
import logging
from pathlib import Path
from typing import Any, Optional, TypeVar, Generic, Callable
from dataclasses import dataclass, field
from enum import Enum
from contextlib import asynccontextmanager
from functools import wraps
from typing_extensions import Self

logger = logging.getLogger("miku.database")


# ============================================================
# 字段类型定义
# ============================================================
class FieldType(Enum):
    TEXT = "TEXT"
    INTEGER = "INTEGER"
    REAL = "REAL"
    BLOB = "BLOB"


@dataclass
class Field:
    """数据库字段定义"""
    field_type: FieldType
    primary_key: bool = False
    default: Any = None
    nullable: bool = False
    unique: bool = False
    auto_increment: bool = False


T = TypeVar("T")


# ============================================================
# 数据库连接池（线程安全）
# ============================================================
class ConnectionPool:
    """SQLite 连接池，支持线程安全操作"""

    def __init__(self, db_path: str, max_connections: int = 5):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.max_connections = max_connections
        self._local = threading.local()
        self._lock = threading.Lock()
        self._connections: list[sqlite3.Connection] = []
        self._init_lock = threading.Lock()

    def _create_connection(self) -> sqlite3.Connection:
        """创建新连接"""
        conn = sqlite3.connect(
            str(self.db_path),
            check_same_thread=False,
            timeout=30.0
        )
        conn.row_factory = sqlite3.Row
        # 启用外键约束
        conn.execute("PRAGMA foreign_keys = ON")
        # 启用 WAL 模式提高并发性能
        conn.execute("PRAGMA journal_mode = WAL")
        return conn

    def get_connection(self) -> sqlite3.Connection:
        """获取当前线程的连接"""
        if not hasattr(self._local, 'connection') or self._local.connection is None:
            with self._lock:
                conn = self._create_connection()
                self._connections.append(conn)
            self._local.connection = conn
        return self._local.connection

    def close_all(self):
        """关闭所有连接"""
        with self._lock:
            for conn in self._connections:
                try:
                    conn.close()
                except Exception:
                    pass
            self._connections.clear()
            if hasattr(self._local, 'connection'):
                self._local.connection = None


# ============================================================
# 模型基类
# ============================================================
class ModelMeta(type):
    """模型元类，自动注册模型"""

    _registry: dict[str, type["Model"]] = {}

    def __new__(mcs, name, bases, namespace, **kwargs):
        cls = super().__new__(mcs, name, bases, namespace)
        if hasattr(cls, 'table_name') and cls.table_name:
            ModelMeta._registry[cls.table_name] = cls
        return cls


class Model(metaclass=ModelMeta):
    """数据库模型基类"""

    table_name: str = ""
    fields: dict[str, Field] = {}

    @classmethod
    def get_all_models(cls) -> dict[str, type["Model"]]:
        """获取所有注册的模型"""
        return ModelMeta._registry.copy()

    @classmethod
    def get_create_table_sql(cls) -> str:
        """生成建表 SQL"""
        columns = []
        for field_name, field_def in cls.fields.items():
            col_sql = f'"{field_name}" {field_def.field_type.value}'

            if field_def.primary_key:
                col_sql += " PRIMARY KEY"
                if field_def.auto_increment:
                    col_sql += " AUTOINCREMENT"

            if not field_def.nullable and not field_def.primary_key:
                col_sql += " NOT NULL"

            if field_def.unique:
                col_sql += " UNIQUE"

            if field_def.default is not None and not field_def.primary_key:
                default_val = field_def.default
                if isinstance(default_val, str):
                    col_sql += f" DEFAULT '{default_val}'"
                else:
                    col_sql += f" DEFAULT {default_val}"

            columns.append(col_sql)

        return f"CREATE TABLE IF NOT EXISTS {cls.table_name} ({', '.join(columns)})"

    @classmethod
    def get_migrations(cls) -> list[str]:
        """获取迁移脚本，子类可重写"""
        return []


# ============================================================
# 异步数据库操作类
# ============================================================
class Database:
    """
    数据库单例类，提供异步操作接口

    使用方式：
    ```python
    db = await Database.get_instance()

    # 同步操作（线程池中执行）
    await db.execute(sql, params)
    row = await db.fetchone(sql, params)
    rows = await db.fetchall(sql, params)

    # 事务操作
    async with db.transaction():
        await db.execute(sql1, params1)
        await db.execute(sql2, params2)

    # 使用模型
    User = await db.get_model("users")
    await db.insert(User, {"user_id": "123", "gold": 100})
    row = await db.select_one(User, "user_id = ?", ("123",))
    ```
    """

    _instance: Optional["Database"] = None
    _init_lock = asyncio.Lock()

    def __init__(self, db_path: str = "data/miku.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.pool = ConnectionPool(str(self.db_path))
        self._executor = asyncio.get_event_loop()
        self._closed = False

    @classmethod
    async def get_instance(cls, db_path: str = "data/miku.db") -> "Database":
        """获取数据库单例"""
        if cls._instance is None:
            async with cls._init_lock:
                if cls._instance is None:
                    cls._instance = cls(db_path)
                    await cls._instance._initialize()
        return cls._instance

    @classmethod
    async def close(cls):
        """关闭数据库连接"""
        if cls._instance:
            cls._instance.pool.close_all()
            cls._instance._closed = True
            cls._instance = None

    async def _run_in_thread(self, func: Callable, *args, **kwargs) -> Any:
        """在线程池中执行同步函数"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, lambda: func(*args, **kwargs))

    async def _initialize(self):
        """初始化数据库，创建表"""
        # 创建所有模型表
        models = Model.get_all_models()
        for table_name, model_cls in models.items():
            sql = model_cls.get_create_table_sql()
            await self.execute(sql)
            logger.info(f"[Database] 表 {table_name} 初始化完成")

        # 执行迁移
        for table_name, model_cls in models.items():
            for migration in model_cls.get_migrations():
                try:
                    await self.execute(migration)
                    logger.info(f"[Database] 迁移完成: {migration[:80]}")
                except (sqlite3.OperationalError, sqlite3.IntegrityError) as e:
                    err_msg = str(e).lower()
                    # 可安全跳过的错误：表示迁移已执行过或目标已不存在（幂等）
                    skippable = (
                        "duplicate column" in err_msg        # ALTER TABLE ADD COLUMN：列已存在
                        or "no such column" in err_msg        # DROP COLUMN / 其他：列不存在
                        or "table already exists" in err_msg  # CREATE TABLE：表已存在
                        or "index already exists" in err_msg  # CREATE INDEX：索引已存在
                        or "there is already an index" in err_msg
                        or "unique constraint failed" in err_msg  # 数据已存在（幂等迁移）
                    )
                    if skippable:
                        logger.debug(
                            f"[Database] 迁移跳过（{err_msg.strip()}）: {migration[:80]}"
                        )
                    else:
                        # 真正的错误：表不存在、语法错误等
                        logger.error(
                            f"[Database] 迁移失败：{migration[:80]}\n"
                            f"  错误类型：{type(e).__name__}\n"
                            f"  错误信息：{e}"
                        )
                        raise

    def get_connection(self) -> sqlite3.Connection:
        """获取数据库连接"""
        return self.pool.get_connection()

    async def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        """执行 SQL（INSERT/UPDATE/DELETE）"""
        def _exec():
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute(sql, params)
            return cursor

        cursor = await self._run_in_thread(_exec)
        return cursor

    async def executemany(self, sql: str, params_list: list[tuple]) -> sqlite3.Cursor:
        """批量执行 SQL"""
        def _exec():
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.executemany(sql, params_list)
            return cursor

        cursor = await self._run_in_thread(_exec)
        return cursor

    async def fetchone(self, sql: str, params: tuple = ()) -> Optional[sqlite3.Row]:
        """查询一条记录"""
        def _fetch():
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute(sql, params)
            return cursor.fetchone()

        return await self._run_in_thread(_fetch)

    async def fetchall(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        """查询所有记录"""
        def _fetch():
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute(sql, params)
            return cursor.fetchall()

        return await self._run_in_thread(_fetch)

    async def fetchmany(self, sql: str, limit: int, offset: int = 0,
                       params: tuple = ()) -> list[sqlite3.Row]:
        """分页查询"""
        sql += f" LIMIT {limit} OFFSET {offset}"
        return await self.fetchall(sql, params)

    async def fetch_count(self, sql: str, params: tuple = ()) -> int:
        """查询数量"""
        row = await self.fetchone(sql, params)
        return row[0] if row else 0

    async def last_row_id(self) -> int:
        """获取最后插入行的ID"""
        def _get():
            conn = self.get_connection()
            return conn.total_changes
        return await self._run_in_thread(_get)

    @asynccontextmanager
    async def transaction(self):
        """事务上下文管理器"""
        def _begin():
            conn = self.get_connection()
            conn.execute("BEGIN TRANSACTION")

        def _commit():
            conn = self.get_connection()
            conn.commit()

        def _rollback():
            conn = self.get_connection()
            conn.rollback()

        await self._run_in_thread(_begin)
        try:
            yield self
            await self._run_in_thread(_commit)
        except Exception:
            await self._run_in_thread(_rollback)
            raise

    # ============================================================
    # 模型操作（简化 ORM）
    # ============================================================
    async def insert(self, model_cls: type[Model], data: dict) -> int:
        """插入记录"""
        fields = list(data.keys())
        placeholders = ["?"] * len(fields)
        sql = f"INSERT INTO {model_cls.table_name} ({', '.join(fields)}) VALUES ({', '.join(placeholders)})"
        values = tuple(data[f] for f in fields)
        await self.execute(sql, values)
        return await self.last_row_id()

    async def update(self, model_cls: type[Model], data: dict,
                    where: str, params: tuple = ()) -> int:
        """更新记录"""
        set_clause = ", ".join([f"{k} = ?" for k in data.keys()])
        sql = f"UPDATE {model_cls.table_name} SET {set_clause} WHERE {where}"
        values = tuple(data[k] for k in data.keys()) + params
        cursor = await self.execute(sql, values)
        return cursor.rowcount

    async def delete(self, model_cls: type[Model], where: str,
                    params: tuple = ()) -> int:
        """删除记录"""
        sql = f"DELETE FROM {model_cls.table_name} WHERE {where}"
        cursor = await self.execute(sql, params)
        return cursor.rowcount

    async def select_one(self, model_cls: type[Model], where: str = "1=1",
                       params: tuple = (), order_by: str = "") -> Optional[dict]:
        """查询单条记录"""
        sql = f"SELECT * FROM {model_cls.table_name} WHERE {where}"
        if order_by:
            sql += f" ORDER BY {order_by}"
        sql += " LIMIT 1"
        row = await self.fetchone(sql, params)
        return dict(row) if row else None

    async def select_all(self, model_cls: type[Model], where: str = "1=1",
                        params: tuple = (), order_by: str = "",
                        limit: int = 0, offset: int = 0) -> list[dict]:
        """查询所有记录"""
        sql = f"SELECT * FROM {model_cls.table_name} WHERE {where}"
        if order_by:
            sql += f" ORDER BY {order_by}"
        if limit > 0:
            sql += f" LIMIT {limit}"
            if offset > 0:
                sql += f" OFFSET {offset}"
        rows = await self.fetchall(sql, params)
        return [dict(row) for row in rows]

    async def exists(self, model_cls: type[Model], where: str,
                   params: tuple = ()) -> bool:
        """检查记录是否存在"""
        sql = f"SELECT 1 FROM {model_cls.table_name} WHERE {where} LIMIT 1"
        row = await self.fetchone(sql, params)
        return row is not None

    async def count(self, model_cls: type[Model], where: str = "1=1",
                   params: tuple = ()) -> int:
        """统计记录数"""
        sql = f"SELECT COUNT(*) FROM {model_cls.table_name} WHERE {where}"
        return await self.fetch_count(sql, params)

    async def upsert(self, model_cls: type[Model], data: dict,
                    unique_fields: list[str]) -> int:
        """
        插入或更新（Upsert）
        unique_fields: 用于判断唯一性的字段列表
        """
        fields = list(data.keys())
        placeholders = ["?"] * len(fields)

        # 构建 ON CONFLICT 子句
        conflict_fields = ", ".join(unique_fields)
        update_fields = [f for f in fields if f not in unique_fields]
        if update_fields:
            set_clause = ", ".join([f"{f} = excluded.{f}" for f in update_fields])
        else:
            set_clause = ", ".join([f"{f} = excluded.{f}" for f in fields])

        sql = f"""
            INSERT INTO {model_cls.table_name} ({', '.join(fields)})
            VALUES ({', '.join(placeholders)})
            ON CONFLICT ({conflict_fields}) DO UPDATE SET {set_clause}
        """

        values = tuple(data[f] for f in fields)
        await self.execute(sql, values)
        return await self.last_row_id()


# ============================================================
# 预定义模型
# ============================================================

class UserModel(Model):
    """用户表"""
    table_name = "users"
    fields = {
        "user_id": Field(FieldType.TEXT, primary_key=True),
        "gold": Field(FieldType.INTEGER, default=100),
        "props": Field(FieldType.TEXT, default="{}"),  # JSON 字符串
        "sign_count": Field(FieldType.INTEGER, default=0),
        "last_sign_time": Field(FieldType.TEXT, nullable=True),  # ISO 格式时间
        "total_spent": Field(FieldType.INTEGER, default=0),
        "total_earned": Field(FieldType.INTEGER, default=0),
        "created_at": Field(FieldType.TEXT),
    }

    @classmethod
    def get_migrations(cls) -> list[str]:
        return [
            # 添加新字段的迁移（如果需要）
        ]


class GroupModel(Model):
    """群组表"""
    table_name = "groups"
    fields = {
        "group_id": Field(FieldType.TEXT, primary_key=True),
        "group_name": Field(FieldType.TEXT),
        "member_count": Field(FieldType.INTEGER, default=0),
        "enabled_plugins": Field(FieldType.TEXT, default="*"),  # * 表示全部启用，JSON 列表表示白名单
        "disabled_plugins": Field(FieldType.TEXT, default="[]"),  # JSON 列表
        "created_at": Field(FieldType.TEXT),
    }


class BanModel(Model):
    """黑名单表"""
    table_name = "ban_list"
    fields = {
        "id": Field(FieldType.INTEGER, primary_key=True, auto_increment=True),
        "user_id": Field(FieldType.TEXT, nullable=True),
        "group_id": Field(FieldType.TEXT, nullable=True),
        "ban_level": Field(FieldType.INTEGER, default=1),
        "ban_time": Field(FieldType.INTEGER),  # Unix 时间戳
        "duration": Field(FieldType.INTEGER),  # 持续分钟数，-1 表示永久
        "reason": Field(FieldType.TEXT, nullable=True),
        "operator_id": Field(FieldType.TEXT),
        "created_at": Field(FieldType.TEXT),
    }


class GoodsModel(Model):
    """商品表"""
    table_name = "goods"
    fields = {
        "uuid": Field(FieldType.TEXT, primary_key=True),
        "name": Field(FieldType.TEXT, unique=True),
        "price": Field(FieldType.INTEGER),
        "description": Field(FieldType.TEXT),
        "discount": Field(FieldType.REAL, default=1.0),
        "stock": Field(FieldType.INTEGER, default=-1),  # -1 表示无限
        "daily_limit": Field(FieldType.INTEGER, default=0),  # 0 表示不限购
        "is_passive": Field(FieldType.INTEGER, default=0),  # 0/1
        "partition": Field(FieldType.TEXT, nullable=True),
        "icon": Field(FieldType.TEXT, nullable=True),
        "created_at": Field(FieldType.TEXT),
    }


class ShopPurchaseLogModel(Model):
    """商店购买记录表"""
    table_name = "shop_purchase_log"
    fields = {
        "id": Field(FieldType.INTEGER, primary_key=True, auto_increment=True),
        "user_id": Field(FieldType.TEXT),
        "goods_name": Field(FieldType.TEXT),
        "price": Field(FieldType.INTEGER),
        "quantity": Field(FieldType.INTEGER, default=1),
        "total_price": Field(FieldType.INTEGER),
        "created_at": Field(FieldType.TEXT),
    }


class GoldLogModel(Model):
    """金币变动日志表"""
    table_name = "gold_log"
    fields = {
        "id": Field(FieldType.INTEGER, primary_key=True, auto_increment=True),
        "user_id": Field(FieldType.TEXT),
        "amount": Field(FieldType.INTEGER),
        "balance": Field(FieldType.INTEGER),
        "handle_type": Field(FieldType.TEXT),  # ADD/REDUCE/SPEND/EARN
        "source": Field(FieldType.TEXT),  # 插件名称
        "memo": Field(FieldType.TEXT, nullable=True),
        "created_at": Field(FieldType.TEXT),
    }


class GroupNoticeModel(Model):
    """群公告表"""
    table_name = "group_notices"
    fields = {
        "id": Field(FieldType.INTEGER, primary_key=True, auto_increment=True),
        "group_id": Field(FieldType.TEXT),
        "title": Field(FieldType.TEXT),
        "content": Field(FieldType.TEXT),
        "created_by": Field(FieldType.TEXT),
        "created_at": Field(FieldType.TEXT),
    }


class SendLikeLogModel(Model):
    """点赞日志表"""
    table_name = "send_like_log"
    fields = {
        "id": Field(FieldType.INTEGER, primary_key=True, auto_increment=True),
        "user_id": Field(FieldType.TEXT),
        "like_count": Field(FieldType.INTEGER),
        "created_at": Field(FieldType.TEXT),
    }


# ============================================================
# 便捷函数
# ============================================================
async def get_db() -> Database:
    """获取数据库实例的便捷函数"""
    return await Database.get_instance()
