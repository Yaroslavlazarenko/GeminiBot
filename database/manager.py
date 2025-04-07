# services/database/manager.py

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
import asyncpg
import logging

from .models import Base

logger = logging.getLogger(__name__)

class DatabaseManager:
    def __init__(self, user: str, password: str, host: str, db_name: str) -> None:
        self.user = user
        self.password = password
        self.host = host
        self.db_name = db_name
        self.async_url = f"postgresql+asyncpg://{user}:{password}@{host}/{db_name}"
        self.base = Base
        # echo=False рекомендуется для продакшена
        self.engine = create_async_engine(self.async_url, echo=False)
        self.async_session_maker = async_sessionmaker(
            bind=self.engine, expire_on_commit=False, class_=AsyncSession
        )
        logger.info(f"DatabaseManager initialized for database '{db_name}' at {host}")

    async def create_database(self):
        """Создает базу данных асинхронно, если она не существует."""
        conn = None
        try:
            logger.info(f"Attempting to connect to 'postgres' db at {self.host} to check/create database '{self.db_name}'")
            conn = await asyncpg.connect(
                user=self.user, password=self.password, host=self.host, database="postgres"
            )
            exists = await conn.fetchval(
                "SELECT 1 FROM pg_database WHERE datname = $1", self.db_name
            )
            if not exists:
                 logger.info(f"Database '{self.db_name}' does not exist. Creating...")
                 try:
                     await conn.execute(f'CREATE DATABASE "{self.db_name}"')
                     logger.info(f"Database '{self.db_name}' created successfully.")
                 except asyncpg.PostgresError as e:
                     logger.error(f"Error creating database '{self.db_name}': {e}", exc_info=True)
            else:
                logger.info(f"Database '{self.db_name}' already exists.")

        except asyncpg.InvalidCatalogNameError:
             logger.warning("Database 'postgres' not found? Cannot check/create database automatically.")
        except asyncpg.PostgresError as e:
            logger.error(f"Error connecting to 'postgres' db or checking database '{self.db_name}': {e}", exc_info=True)
        except Exception as e:
             logger.error(f"Unexpected error during database check/creation: {e}", exc_info=True)
        finally:
            if conn:
                await conn.close()
                logger.debug("Connection to 'postgres' db closed.")


    async def create_tables(self) -> None:
        """Создает таблицы в базе данных."""
        logger.info(f"Attempting to create tables defined in Base.metadata for database '{self.db_name}'...")
        try:
            async with self.engine.begin() as conn:
                await conn.run_sync(self.base.metadata.create_all)
            logger.info("Tables checked/created successfully.")
        except Exception as e:
            logger.critical(f"FATAL: Could not create tables in database '{self.db_name}': {e}", exc_info=True)
            raise

    def get_session_factory(self) -> async_sessionmaker[AsyncSession]:
        """Возвращает фабрику сессий."""
        return self.async_session_maker

    async def dispose_engine(self) -> None:
        """Закрывает пул соединений движка."""
        logger.info("Disposing database engine connections...")
        await self.engine.dispose()
        logger.info("Database engine connections disposed.")