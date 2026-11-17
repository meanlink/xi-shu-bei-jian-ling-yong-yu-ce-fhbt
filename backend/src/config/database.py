from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from .settings import DATABASE_URL

class Base(DeclarativeBase):
    pass

engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_recycle=1800)

if engine.dialect.name == 'sqlite':
    @event.listens_for(engine, 'connect')
    def _sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute('PRAGMA busy_timeout=5000')
        cursor.execute('PRAGMA foreign_keys=ON')
        cursor.close()

    # 官方事件配方：在每个事务真正开始时把 SQLite 的惰性 BEGIN 替换为 BEGIN IMMEDIATE，
    # 并发认领/试算在 SQLite 上直接串行化，避免 deferred 锁升级造成死锁；PG 走行锁。
    @event.listens_for(engine, 'begin')
    def _sqlite_begin_immediate(connection):
        connection.connection.dbapi_connection.cursor().execute('BEGIN IMMEDIATE')

Session = sessionmaker(engine, expire_on_commit=False)

def get_db():
    with Session() as db:
        yield db
