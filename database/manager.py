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
        
        # Optimized async connection pool settings
        self.engine = create_async_engine(
            self.async_url,
            echo=False,
            # Connection pool settings
            pool_size=10,
            max_overflow=5,
            pool_timeout=20,
            pool_recycle=300,  # Recycle connections every 5 minutes
            pool_pre_ping=True,  # Connection health checks
            pool_use_lifo=True,  # Better handling of disconnects
            json_serializer=None,  # Disable JSON serialization overhead
        )
        
        self.async_session_maker = async_sessionmaker(
            bind=self.engine,
            expire_on_commit=False,
            class_=AsyncSession,
            autoflush=True,
            autocommit=False
        )
        logger.debug(f"DatabaseManager initialized for database '{db_name}' at {host}")

    async def create_database(self):
        """Создает базу данных асинхронно, если она не существует."""
        conn = None
        try:
            conn = await asyncpg.connect(
                user=self.user, password=self.password, host=self.host, database="postgres"
            )
            exists = await conn.fetchval(
                "SELECT 1 FROM pg_database WHERE datname = $1", self.db_name
            )
            if not exists:
                logger.info(f"Creating database '{self.db_name}'...")
                try:
                    await conn.execute(f'CREATE DATABASE "{self.db_name}"')
                    logger.info(f"Database '{self.db_name}' created successfully.")
                except asyncpg.PostgresError as e:
                    logger.critical(f"Error creating database '{self.db_name}': {e}", exc_info=True)
            else:
                logger.debug(f"Database '{self.db_name}' already exists.")

        except asyncpg.InvalidCatalogNameError:
            logger.critical("Database 'postgres' not found. Cannot check/create database automatically.")
        except asyncpg.PostgresError as e:
            logger.critical(f"Error connecting to 'postgres' db or checking database '{self.db_name}': {e}", exc_info=True)
        except Exception as e:
            logger.critical(f"Unexpected error during database check/creation: {e}", exc_info=True)
        finally:
            if conn:
                await conn.close()

    async def create_tables(self) -> None:
        """Создает таблицы в базе данных."""
        try:
            async with self.engine.begin() as conn:
                await conn.run_sync(self.base.metadata.create_all)
            logger.info("Database tables created successfully.")
        except Exception as e:
            logger.critical(f"FATAL: Could not create tables in database '{self.db_name}': {e}", exc_info=True)
            raise

    def get_session_factory(self) -> async_sessionmaker[AsyncSession]:
        """Возвращает фабрику сессий."""
        return self.async_session_maker

    async def dispose_engine(self) -> None:
        """Закрывает пул соединений движка."""
        await self.engine.dispose()
        logger.debug("Database engine connections disposed.")