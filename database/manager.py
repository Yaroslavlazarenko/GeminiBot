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
        self._engine = None
        self._async_session_maker = None
        
    async def __aenter__(self):
        """Async context manager entry."""
        await self.initialize()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.dispose_engine()

    async def initialize(self):
        """Initialize database engine and session maker."""
        if self._engine is None:
            self._engine = create_async_engine(
                self.async_url,
                echo=False,
                pool_size=10,
                max_overflow=5,
                pool_timeout=20,
                pool_recycle=300,
                pool_pre_ping=True,
                pool_use_lifo=True,
                json_serializer=None
            )
            
            self._async_session_maker = async_sessionmaker(
                bind=self._engine,
                expire_on_commit=False,
                class_=AsyncSession,
                autoflush=True,
                autocommit=False
            )
            logger.debug(f"DatabaseManager initialized for database '{self.db_name}' at {self.host}")

    @property
    def engine(self):
        """Get the database engine."""
        if self._engine is None:
            raise RuntimeError("Database engine not initialized. Call initialize() first.")
        return self._engine

    async def create_database(self):
        """Creates database if it doesn't exist."""
        conn = None
        try:
            conn = await asyncpg.connect(
                user=self.user,
                password=self.password,
                host=self.host,
                database="postgres"
            )
            exists = await conn.fetchval(
                "SELECT 1 FROM pg_database WHERE datname = $1",
                self.db_name
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
        except Exception as e:
            logger.critical(f"Error during database creation: {e}", exc_info=True)
            raise
        finally:
            if conn:
                await conn.close()

    async def create_tables(self) -> None:
        """Creates tables in the database."""
        try:
            async with self.engine.begin() as conn:
                await conn.run_sync(self.base.metadata.create_all)
            logger.info("Database tables created successfully.")
        except Exception as e:
            logger.critical(f"Could not create tables in database '{self.db_name}': {e}", exc_info=True)
            raise

    def get_session_factory(self) -> async_sessionmaker[AsyncSession]:
        """Returns the session factory."""
        if self._async_session_maker is None:
            raise RuntimeError("Session factory not initialized. Call initialize() first.")
        return self._async_session_maker

    async def dispose_engine(self) -> None:
        """Closes the connection pool."""
        if self._engine:
            try:
                await self._engine.dispose()
                logger.debug("Database engine connections disposed.")
            except Exception as e:
                logger.error(f"Error disposing database engine: {e}", exc_info=True)
            finally:
                self._engine = None
                self._async_session_maker = None